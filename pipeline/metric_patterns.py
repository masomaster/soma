"""Layer 3 cross-metric correlations (Phase 8c) — stdlib Pearson, no SciPy.

This module pre-computes deterministic Pearson correlations between the athlete's
metrics so the LLM can *narrate confirmed relationships* rather than reason over
raw history (see the architecture rule in ``.cursor/rules/soma.mdc``: "The LLM
explains pre-computed conclusions. It never reasons over raw data.").

Two families of pairs are computed:

* **Within-table** pairs (:data:`PAIR_TARGETS`) live entirely inside
  ``daily_health_metrics`` — sleep vs HRV, sleep vs resting HR, steps vs active
  calories.
* **Cross-table** pairs (:data:`CROSS_PAIR_TARGETS`) span tables: a recovery
  metric from ``daily_health_metrics`` (e.g. ``sleep_hours``) against a training
  outcome from ``daily_features`` (e.g. ``cardio_minutes_7d``,
  ``strength_tonnage_7d``, ``overall_readiness_score``). The two per-day series
  are read into aligned, date-keyed maps **in Python** and correlated with the
  same stdlib helper — never via a SQL ``JOIN`` (the bounded read path forbids
  joins on purpose).

All results share the ``metric_patterns`` row shape, so cross-table pairs need no
schema change: their ``metric_b`` names (``*_7d`` / ``overall_readiness_score``)
are distinct from the within-table metric names, so the
``UNIQUE (user_id, metric_a, metric_b, lag_days)`` constraint never collides.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.features import as_date

# Within-table pairs: both metrics come from ``daily_health_metrics``.
PAIR_TARGETS: tuple[tuple[str, str], ...] = (
    ("sleep_hours", "hrv_rmssd"),
    ("sleep_hours", "resting_hr"),
    ("steps", "active_cal"),
)

# Cross-table pairs: metric_a from ``daily_health_metrics`` (recovery/biometric),
# metric_b from ``daily_features`` (training outcome / readiness). Positive lag
# means metric_a on day D is correlated with metric_b on day D+lag, i.e. sleep
# today vs training/readiness in the following days.
CROSS_PAIR_TARGETS: tuple[tuple[str, str], ...] = (
    ("sleep_hours", "cardio_minutes_7d"),
    ("sleep_hours", "strength_tonnage_7d"),
    ("sleep_hours", "overall_readiness_score"),
    ("sleep_hours", "effort_unified_index_7d"),
    ("hrv_rmssd", "overall_readiness_score"),
)

# Date column names for the two source tables.
_METRICS_DATE_KEY = "metric_date"
_FEATURES_DATE_KEY = "feature_date"

MAX_LAG_DAYS = 2
MIN_SAMPLE_N = 14
MAX_PATTERNS_PER_USER = 12
MIN_ABS_CORRELATION = 0.45

# Plain-English labels used only in the human-readable ``description`` field; the
# stored ``metric_a`` / ``metric_b`` keep the canonical metric names.
_METRIC_LABELS: dict[str, str] = {
    "sleep_hours": "sleep hours",
    "hrv_rmssd": "HRV (rMSSD)",
    "resting_hr": "resting HR",
    "steps": "steps",
    "active_cal": "active calories",
    "cardio_minutes_7d": "7d cardio minutes",
    "strength_tonnage_7d": "7d strength tonnage",
    "overall_readiness_score": "readiness score",
    "effort_unified_index_7d": "7d unified effort",
}


def _label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric)


def _series_by_date(
    history: Sequence[Mapping[str, Any]],
    metric: str,
    *,
    before: date,
    days: int,
    date_key: str = _METRICS_DATE_KEY,
) -> dict[date, float]:
    """Return ``{date: value}`` for ``metric`` within ``[before - days, before)``.

    ``date_key`` selects the source table's date column (``metric_date`` for
    ``daily_health_metrics``, ``feature_date`` for ``daily_features``).
    """
    start = before - timedelta(days=days)
    out: dict[date, float] = {}
    for row in history:
        d = as_date(row.get(date_key))
        if d is None or d < start or d >= before:
            continue
        raw = row.get(metric)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            out[d] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / (den_x * den_y)


def _correlate_lagged(
    a_map: Mapping[date, float],
    b_map: Mapping[date, float],
    *,
    lag: int,
) -> tuple[float, int] | None:
    """Align two date-keyed series at ``lag`` days and return ``(r, n)``.

    Applies :data:`MIN_SAMPLE_N` and :data:`MIN_ABS_CORRELATION`; returns
    ``None`` when the pair is too sparse or too weak to report.
    """
    xs: list[float] = []
    ys: list[float] = []
    for d, av in a_map.items():
        bv = b_map.get(d + timedelta(days=lag))
        if bv is not None:
            xs.append(av)
            ys.append(bv)
    if len(xs) < MIN_SAMPLE_N:
        return None
    r = _pearson(xs, ys)
    if r is None or abs(r) < MIN_ABS_CORRELATION:
        return None
    return r, len(xs)


def _pattern_row(
    *,
    user_id: str,
    metric_a: str,
    metric_b: str,
    lag: int,
    r: float,
    n: int,
) -> dict[str, Any]:
    """Build a ``metric_patterns`` row from a computed correlation."""
    direction = "positive" if r > 0 else "negative"
    return {
        "user_id": user_id,
        "metric_a": metric_a,
        "metric_b": metric_b,
        "lag_days": lag,
        "correlation": round(r, 4),
        "sample_n": n,
        "status": "active",
        "description": (
            f"{_label(metric_a)} vs {_label(metric_b)} (lag {lag}d): "
            f"r={round(r, 3)} ({direction}, n={n})"
        ),
    }


def compute_metric_patterns(
    *,
    user_id: str,
    as_of: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
    lookback_days: int = 60,
) -> list[dict[str, Any]]:
    """Return within-table ``metric_patterns`` rows (caller persists)."""
    patterns: list[dict[str, Any]] = []
    for metric_a, metric_b in PAIR_TARGETS:
        a_map = _series_by_date(
            daily_metrics_history, metric_a, before=as_of, days=lookback_days
        )
        b_map = _series_by_date(
            daily_metrics_history, metric_b, before=as_of, days=lookback_days
        )
        for lag in range(0, MAX_LAG_DAYS + 1):
            result = _correlate_lagged(a_map, b_map, lag=lag)
            if result is None:
                continue
            r, n = result
            patterns.append(
                _pattern_row(
                    user_id=user_id,
                    metric_a=metric_a,
                    metric_b=metric_b,
                    lag=lag,
                    r=r,
                    n=n,
                )
            )
    patterns.sort(key=lambda p: abs(float(p["correlation"])), reverse=True)
    return patterns[:MAX_PATTERNS_PER_USER]


def compute_cross_metric_patterns(
    *,
    user_id: str,
    as_of: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
    daily_features_history: Sequence[Mapping[str, Any]],
    lookback_days: int = 60,
) -> list[dict[str, Any]]:
    """Return cross-table ``metric_patterns`` rows (recovery vs training outcome).

    ``metric_a`` series come from ``daily_health_metrics`` rows
    (``daily_metrics_history``) and ``metric_b`` series from ``daily_features``
    rows (``daily_features_history``). The two are aligned by calendar date in
    Python — no SQL join — and correlated with the same lag-aware Pearson helper
    as :func:`compute_metric_patterns`.
    """
    patterns: list[dict[str, Any]] = []
    for metric_a, metric_b in CROSS_PAIR_TARGETS:
        a_map = _series_by_date(
            daily_metrics_history, metric_a, before=as_of, days=lookback_days
        )
        b_map = _series_by_date(
            daily_features_history,
            metric_b,
            before=as_of,
            days=lookback_days,
            date_key=_FEATURES_DATE_KEY,
        )
        for lag in range(0, MAX_LAG_DAYS + 1):
            result = _correlate_lagged(a_map, b_map, lag=lag)
            if result is None:
                continue
            r, n = result
            patterns.append(
                _pattern_row(
                    user_id=user_id,
                    metric_a=metric_a,
                    metric_b=metric_b,
                    lag=lag,
                    r=r,
                    n=n,
                )
            )
    patterns.sort(key=lambda p: abs(float(p["correlation"])), reverse=True)
    return patterns[:MAX_PATTERNS_PER_USER]


def compute_all_metric_patterns(
    *,
    user_id: str,
    as_of: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
    daily_features_history: Sequence[Mapping[str, Any]] | None = None,
    lookback_days: int = 60,
) -> list[dict[str, Any]]:
    """Compute within-table and cross-table patterns, merged and capped.

    When ``daily_features_history`` is omitted or empty only within-table pairs
    are computed, so existing callers keep working. The merged list is
    deduplicated by ``(metric_a, metric_b, lag_days)``, sorted by absolute
    correlation, and capped at :data:`MAX_PATTERNS_PER_USER`.
    """
    combined = compute_metric_patterns(
        user_id=user_id,
        as_of=as_of,
        daily_metrics_history=daily_metrics_history,
        lookback_days=lookback_days,
    )
    if daily_features_history:
        combined += compute_cross_metric_patterns(
            user_id=user_id,
            as_of=as_of,
            daily_metrics_history=daily_metrics_history,
            daily_features_history=daily_features_history,
            lookback_days=lookback_days,
        )
    seen: set[tuple[str, str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for p in sorted(combined, key=lambda p: abs(float(p["correlation"])), reverse=True):
        key = (p["metric_a"], p["metric_b"], int(p["lag_days"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped[:MAX_PATTERNS_PER_USER]


def active_pattern_summaries(patterns: Sequence[Mapping[str, Any]]) -> list[str]:
    """Short strings for briefing ``active_patterns`` block."""
    out: list[str] = []
    for p in patterns:
        if p.get("status") != "active":
            continue
        desc = p.get("description")
        if isinstance(desc, str) and desc.strip():
            out.append(desc.strip())
    return out

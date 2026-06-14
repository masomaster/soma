"""Deterministic statistical signals for recovery metrics (Phase 8, slices 1–3).

Computes **z-scores** vs a trailing baseline from ``daily_health_metrics`` wide
rows — no SciPy dependency (stdlib ``statistics`` only). Intended output is fed
to :func:`pipeline.briefing.build_prompt` and stored in briefing ``features_json``
under ``stat_signals`` for auditability.

The LLM must narrate these pre-computed rows; it does not derive them.

Rows for ``anomaly_events`` (``anomaly_type = 'statistical'``) are built via
:func:`build_statistical_anomaly_rows` and written with
:func:`pipeline.persistence.replace_statistical_anomaly_events`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from statistics import mean, stdev
from typing import Any

from pipeline.features import as_date

# Metrics with roughly symmetric daily variation; IQR/EWMA can follow later.
Z_SCORE_METRICS: tuple[str, ...] = ("hrv_rmssd", "sleep_hours", "resting_hr")

# Require this many non-null **prior** days before flagging (sparse recovery).
MIN_BASELINE_DAYS = 14

# Flag when |z| exceeds this threshold (two-tailed).
DEFAULT_Z_THRESHOLD = 2.0

# Persisted ``anomaly_events.severity`` when |z| is at or above this magnitude.
Z_SEVERITY_ALERT_THRESHOLD = 3.0


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    return None


def _baseline_series(
    history: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    before: date,
) -> list[float]:
    """Values strictly before ``before`` with non-null ``metric``."""
    out: list[float] = []
    for row in history:
        d = as_date(row.get("metric_date"))
        if d is None or d >= before:
            continue
        v = _num(row.get(metric))
        if v is not None:
            out.append(v)
    return out


def compute_statistical_signals(
    *,
    feature_date: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
    today_metrics: Mapping[str, Any],
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_baseline_days: int = MIN_BASELINE_DAYS,
) -> dict[str, Any]:
    """Return ``{"anomalies": [...], "trends": []}`` for briefing consumption.

    Baseline uses only rows with ``metric_date`` **strictly before**
    ``feature_date``. Today's value is read from ``today_metrics`` (same source
    as rollup). ``trends`` is reserved for EWMA/drift (empty in slices 1–3).

    ``stdev`` needs at least two baseline points; with fewer than
    ``min_baseline_days`` samples the metric is skipped. Zero sample variance
    yields no z-score (no flag).
    """
    anomalies: list[dict[str, Any]] = []
    for metric in Z_SCORE_METRICS:
        baseline = _baseline_series(daily_metrics_history, metric=metric, before=feature_date)
        if len(baseline) < min_baseline_days:
            continue
        if len(baseline) < 2:
            continue
        today = _num(today_metrics.get(metric))
        if today is None:
            continue
        m = mean(baseline)
        s = stdev(baseline)
        if s == 0.0:
            continue
        z = (today - m) / s
        if abs(z) <= z_threshold:
            continue
        direction = "below_baseline" if z < 0 else "above_baseline"
        anomalies.append(
            {
                "metric": metric,
                "value": today,
                "baseline_mean": round(m, 4),
                "baseline_stdev": round(s, 4),
                "baseline_n": len(baseline),
                "z_score": round(z, 3),
                "method": "z_score",
                "direction": direction,
            }
        )
    return {"anomalies": anomalies, "trends": []}


def build_statistical_anomaly_rows(
    *,
    user_id: str,
    detected_date: date,
    stat_signals: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Shape ``stat_signals["anomalies"]`` into ``anomaly_events`` insert dicts.

    Each row includes a short ``description`` for operators and full numeric
    detail in ``context_json`` (mirrors the in-memory anomaly dict).
    """
    rows: list[dict[str, Any]] = []
    raw = stat_signals.get("anomalies")
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        if not isinstance(metric, str) or not metric:
            continue
        z = item.get("z_score")
        z_val = _num(z) if z is not None else None
        mean_v = item.get("baseline_mean")
        n = item.get("baseline_n")
        desc = (
            f"{metric} z-score {z} vs prior baseline "
            f"(mean {mean_v}, n={n})"
        )
        if len(desc) > 500:
            desc = desc[:497] + "..."
        severity = (
            "alert"
            if z_val is not None and abs(z_val) >= Z_SEVERITY_ALERT_THRESHOLD
            else "info"
        )
        rows.append(
            {
                "user_id": user_id,
                "detected_date": detected_date,
                "metric": metric,
                "anomaly_type": "statistical",
                "description": desc,
                "severity": severity,
                "context_json": dict(item),
            }
        )
    return rows

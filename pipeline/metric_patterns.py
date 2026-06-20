"""Layer 3 cross-metric correlations (Phase 8c) — stdlib Pearson, no SciPy."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.features import as_date

PAIR_TARGETS: tuple[tuple[str, str], ...] = (
    ("sleep_hours", "hrv_rmssd"),
    ("sleep_hours", "resting_hr"),
    ("steps", "active_cal"),
)

MAX_LAG_DAYS = 2
MIN_SAMPLE_N = 14
MAX_PATTERNS_PER_USER = 12
MIN_ABS_CORRELATION = 0.45


def _series_by_date(
    history: Sequence[Mapping[str, Any]],
    metric: str,
    *,
    before: date,
    days: int,
) -> dict[date, float]:
    start = before - timedelta(days=days)
    out: dict[date, float] = {}
    for row in history:
        d = as_date(row.get("metric_date"))
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


def compute_metric_patterns(
    *,
    user_id: str,
    as_of: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
    lookback_days: int = 60,
) -> list[dict[str, Any]]:
    """Return candidate ``metric_patterns`` rows (caller persists)."""
    patterns: list[dict[str, Any]] = []
    for metric_a, metric_b in PAIR_TARGETS:
        for lag in range(0, MAX_LAG_DAYS + 1):
            a_map = _series_by_date(daily_metrics_history, metric_a, before=as_of, days=lookback_days)
            b_map = _series_by_date(daily_metrics_history, metric_b, before=as_of, days=lookback_days)
            xs: list[float] = []
            ys: list[float] = []
            for d, av in a_map.items():
                bd = d + timedelta(days=lag)
                bv = b_map.get(bd)
                if bv is not None:
                    xs.append(av)
                    ys.append(bv)
            if len(xs) < MIN_SAMPLE_N:
                continue
            r = _pearson(xs, ys)
            if r is None or abs(r) < MIN_ABS_CORRELATION:
                continue
            direction = "positive" if r > 0 else "negative"
            patterns.append(
                {
                    "user_id": user_id,
                    "metric_a": metric_a,
                    "metric_b": metric_b,
                    "lag_days": lag,
                    "correlation": round(r, 4),
                    "sample_n": len(xs),
                    "status": "active",
                    "description": (
                        f"{metric_a} vs {metric_b} (lag {lag}d): r={round(r, 3)} ({direction}, n={len(xs)})"
                    ),
                }
            )
    patterns.sort(key=lambda p: abs(float(p["correlation"])), reverse=True)
    return patterns[:MAX_PATTERNS_PER_USER]


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

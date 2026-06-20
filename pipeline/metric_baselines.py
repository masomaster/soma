"""Layer 1 rolling baselines from ``daily_health_metrics`` history (Phase 8a)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Any

from pipeline.features import DAILY_HEALTH_METRIC_COLUMNS, as_date

BASELINE_WINDOWS: tuple[int, ...] = (7, 28, 90)
BASELINE_METRICS: tuple[str, ...] = (
    "hrv_rmssd",
    "sleep_hours",
    "resting_hr",
    "steps",
    "active_cal",
    "body_weight_lbs",
)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    return None


def compute_metric_baselines(
    *,
    user_id: str,
    metric_date: date,
    daily_metrics_history: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return rows for ``metric_baselines`` upsert (trailing windows ending before ``metric_date``)."""
    rows: list[dict[str, Any]] = []
    for window in BASELINE_WINDOWS:
        start = metric_date - timedelta(days=window)
        for metric in BASELINE_METRICS:
            vals: list[float] = []
            for row in daily_metrics_history:
                d = as_date(row.get("metric_date"))
                if d is None or d < start or d >= metric_date:
                    continue
                v = _num(row.get(metric))
                if v is not None:
                    vals.append(v)
            if len(vals) < 2:
                continue
            m = mean(vals)
            s = stdev(vals) if len(vals) > 1 else 0.0
            rows.append(
                {
                    "user_id": user_id,
                    "metric_date": metric_date,
                    "metric": metric,
                    "window_days": window,
                    "mean_value": round(m, 4),
                    "stdev_value": round(s, 4),
                    "sample_n": len(vals),
                }
            )
    return rows

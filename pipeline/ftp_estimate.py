"""Estimate FTP from stored ride MMP curves and persist to ``daily_health_metrics``."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.persistence import upsert_row
from pipeline.power_math import (
    DEFAULT_FTP_LOOKBACK_DAYS,
    aggregate_best_mmp,
    estimate_ftp_from_best_mmp,
)

logger = logging.getLogger(__name__)


def _coerce_mmp(raw: Any) -> dict[str, float] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


def collect_mmp_maps(cardio_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    """Extract ``power_mmp_json`` maps from cardio rows (skips missing/empty)."""
    maps: list[dict[str, float]] = []
    for row in cardio_rows:
        mmp = _coerce_mmp(row.get("power_mmp_json"))
        if mmp:
            maps.append(mmp)
    return maps


def estimate_ftp_for_rides(
    cardio_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate best MMP across rides and return an FTP estimate dict."""
    best = aggregate_best_mmp(collect_mmp_maps(cardio_rows))
    return estimate_ftp_from_best_mmp(best)


def load_power_rides(
    cur: Any,
    *,
    user_id: str,
    as_of: date,
    lookback_days: int = DEFAULT_FTP_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """Load cardio rows with MMP in the lookback window ending on ``as_of``."""
    start = as_of - timedelta(days=max(1, lookback_days) - 1)
    cur.execute(
        """
        SELECT source, source_id, event_date, power_mmp_json, avg_watts, duration_min
        FROM cardio_events
        WHERE user_id = %s::uuid
          AND event_date BETWEEN %s AND %s
          AND power_mmp_json IS NOT NULL
        """,
        (user_id, start, as_of),
    )
    cols = ("source", "source_id", "event_date", "power_mmp_json", "avg_watts", "duration_min")
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def persist_ftp_estimate(
    cur: Any,
    *,
    user_id: str,
    metric_date: date,
    estimate: Mapping[str, Any],
) -> None:
    """Sparse upsert of ``ftp_*`` columns onto ``daily_health_metrics``."""
    row = {
        "user_id": user_id,
        "metric_date": metric_date,
        "ftp_watts": estimate.get("ftp_watts"),
        "ftp_method": estimate.get("ftp_method"),
        "ftp_confidence": estimate.get("ftp_confidence"),
    }
    upsert_row(cur, "daily_health_metrics", row)
    logger.info(
        "Persisted FTP estimate for %s on %s: method=%s watts=%s conf=%s",
        user_id,
        metric_date,
        row["ftp_method"],
        row["ftp_watts"],
        row["ftp_confidence"],
    )


def estimate_and_persist_ftp(
    cur: Any,
    *,
    user_id: str,
    as_of: date,
    lookback_days: int = DEFAULT_FTP_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Load recent MMP rides, estimate FTP, persist, and return the estimate."""
    rides = load_power_rides(cur, user_id=user_id, as_of=as_of, lookback_days=lookback_days)
    estimate = estimate_ftp_for_rides(rides)
    persist_ftp_estimate(cur, user_id=user_id, metric_date=as_of, estimate=estimate)
    return estimate

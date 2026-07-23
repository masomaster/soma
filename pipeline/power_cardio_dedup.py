"""Cross-source near-duplicate dedup for power-rich FIT cardio rows.

When a Wahoo Dropbox FIT or Strava export ride overlaps an Apple Health (or legacy
Strava summary) copy of the same session, keep the higher-priority source
(:data:`pipeline.power_source_priority.POWER_CARDIO_SOURCE_PRIORITY`) so cardio
minutes are not double-counted and watts win.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from pipeline.apple_health_cardio_dedup import activity_family
from pipeline.features import as_date
from pipeline.power_source_priority import power_cardio_source_rank
from pipeline.timeparse import ensure_utc, parse_iso_datetime_utc

logger = logging.getLogger(__name__)

_START_TOL_MIN = 15.0
_DURATION_TOL_MIN = 5.0

# Sources that participate in power-aware cross-source dedup.
_POWER_DEDUP_SOURCES = frozenset({"wahoo_fit", "strava_export", "apple_health", "strava"})


def _start_dt(row: dict[str, Any]) -> datetime | None:
    v = row.get("started_at")
    if isinstance(v, datetime):
        return ensure_utc(v)
    return parse_iso_datetime_utc(v)


def _row_richness(row: dict[str, Any]) -> int:
    score = 0
    if row.get("avg_watts") is not None or row.get("power_mmp_json"):
        score += 5
    if row.get("distance_miles") is not None:
        score += 2
    if row.get("avg_hr") is not None:
        score += 1
    if row.get("max_hr") is not None:
        score += 1
    if row.get("elevation_ft") is not None:
        score += 1
    return score


def _resolution_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        power_cardio_source_rank(row.get("source")),
        _row_richness(row),
        str(row.get("source_id") or ""),
    )


def near_duplicate_power_cardio(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two cardio rows (any allowed source) look like the same session."""
    src_a = str(a.get("source") or "")
    src_b = str(b.get("source") or "")
    if src_a not in _POWER_DEDUP_SOURCES or src_b not in _POWER_DEDUP_SOURCES:
        return False
    if src_a == src_b and a.get("source_id") == b.get("source_id"):
        return True
    da = as_date(a.get("event_date"))
    db = as_date(b.get("event_date"))
    if da is None or da != db:
        return False
    if activity_family(a.get("activity_type")) != activity_family(b.get("activity_type")):
        return False
    start_a = _start_dt(a)
    start_b = _start_dt(b)
    if start_a is not None and start_b is not None:
        if abs((start_a - start_b).total_seconds()) <= _START_TOL_MIN * 60.0:
            return True
        return False
    try:
        dur_a = float(a.get("duration_min") or 0)
        dur_b = float(b.get("duration_min") or 0)
    except (TypeError, ValueError):
        return False
    return abs(dur_a - dur_b) <= _DURATION_TOL_MIN


def filter_power_cardio_duplicates(
    incoming: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep highest-priority rows; return ``(rows_to_upsert, superseded_source_ids)``.

    ``superseded_source_ids`` are **existing** rows that lose to an incoming FIT
    row and should be deleted before upsert.
    """
    if not incoming:
        return [], []
    # Cluster incoming among themselves first.
    kept_in: list[dict[str, Any]] = []
    for row in sorted(incoming, key=_resolution_key, reverse=True):
        if any(near_duplicate_power_cardio(row, k) for k in kept_in):
            continue
        kept_in.append(row)

    superseded: list[str] = []
    final: list[dict[str, Any]] = []
    for row in kept_in:
        losers: list[dict[str, Any]] = []
        blocked = False
        for ex in existing:
            if not near_duplicate_power_cardio(row, ex):
                continue
            if _resolution_key(row) > _resolution_key(ex):
                sid = ex.get("source_id")
                if isinstance(sid, str) and sid:
                    losers.append(ex)
            else:
                # Existing outranks or ties richer — drop incoming.
                blocked = True
                break
        if blocked:
            continue
        for ex in losers:
            sid = ex.get("source_id")
            if isinstance(sid, str) and sid and sid not in superseded:
                superseded.append(sid)
        final.append(row)
    if superseded:
        logger.info(
            "Power cardio dedup: %d incoming kept, %d existing source_id(s) superseded",
            len(final),
            len(superseded),
        )
    return final, superseded


def load_existing_cardio_for_dates(
    cur: Any,
    *,
    user_id: str,
    dates: list[date],
) -> list[dict[str, Any]]:
    """Load cardio rows on ``dates`` that participate in power dedup."""
    if not dates:
        return []
    cur.execute(
        """
        SELECT source, source_id, event_date, activity_type, duration_min,
               distance_miles, elevation_ft, avg_hr, max_hr, started_at,
               avg_watts, power_mmp_json
        FROM cardio_events
        WHERE user_id = %s::uuid
          AND event_date = ANY(%s::date[])
          AND source = ANY(%s)
        """,
        (user_id, dates, list(_POWER_DEDUP_SOURCES)),
    )
    cols = (
        "source",
        "source_id",
        "event_date",
        "activity_type",
        "duration_min",
        "distance_miles",
        "elevation_ft",
        "avg_hr",
        "max_hr",
        "started_at",
        "avg_watts",
        "power_mmp_json",
    )
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

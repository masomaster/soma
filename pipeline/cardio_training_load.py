"""Filter cardio rows for training-load minutes (pace lights, features).

Ingest-time dedup keeps Fitbit-only walks as activity days, but those NEAT walks
must not inflate "cardio minutes" / overload lights. This module is pure and
safe to call at query time over already-loaded ``cardio_events``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pipeline.apple_health_cardio_dedup import (
    activity_family,
    apple_cardio_rows_to_drop,
)
from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.power_cardio_dedup import filter_power_cardio_duplicates
from pipeline.timeparse import ensure_utc, parse_iso_datetime_utc
from pipeline.workout_calendar import is_fitbit_origin_activity

_POWER_DEDUP_SOURCES = frozenset({"wahoo_fit", "strava_export", "apple_health", "strava"})


def is_bridge_neat_walk(row: Mapping[str, Any]) -> bool:
    """True for Fitbit/Health Sync / Google Fit walks (NEAT, not training)."""
    if not is_fitbit_origin_activity(row.get("source_app")):
        return False
    return activity_family(row.get("activity_type")) == "walk"


def counts_toward_cardio_training_load(row: Mapping[str, Any]) -> bool:
    """Whether a cardio row should contribute minutes/miles to training load."""
    if is_strength_like_cardio_activity(row.get("activity_type")):
        return False
    if is_bridge_neat_walk(row):
        return False
    return True


def _start_dt(row: Mapping[str, Any]) -> datetime | None:
    v = row.get("started_at")
    if isinstance(v, datetime):
        return ensure_utc(v)
    return parse_iso_datetime_utc(v)


def _dedupe_cross_source_power(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse Wahoo/Strava/Apple mirrors that share a start time.

    Rows without ``started_at`` are left alone: the ingest helper's duration-only
    fallback is too aggressive for query-time load and can drop two real same-day
    sessions. Apple hub dups are already handled separately.
    """
    power_rows = [r for r in rows if str(r.get("source") or "") in _POWER_DEDUP_SOURCES]
    other = [r for r in rows if str(r.get("source") or "") not in _POWER_DEDUP_SOURCES]
    with_start = [r for r in power_rows if _start_dt(r) is not None]
    without_start = [r for r in power_rows if _start_dt(r) is None]
    if len(with_start) < 2:
        return rows
    kept, _ = filter_power_cardio_duplicates(with_start, [])
    return kept + without_start + other


def filter_cardio_for_training_load(
    cardio_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop NEAT walks + strength mirrors, then collapse near-duplicate sessions.

    Apple Health hub dups (NRC/Strava/Fitbit mirrors) use ingest cleanup rules.
    Cross-source power dups collapse only when start times are present.
    """
    eligible = [
        dict(row)
        for row in cardio_events
        if counts_toward_cardio_training_load(row)
    ]
    if len(eligible) < 2:
        return eligible

    apple = [r for r in eligible if str(r.get("source") or "") == "apple_health"]
    other = [r for r in eligible if str(r.get("source") or "") != "apple_health"]
    if len(apple) >= 2:
        drop_ids = {id(r) for r in apple_cardio_rows_to_drop(apple)}
        apple = [r for r in apple if id(r) not in drop_ids]
    return _dedupe_cross_source_power(apple + other)

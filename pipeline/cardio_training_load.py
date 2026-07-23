"""Filter cardio rows for training-load minutes (pace lights, features).

Ingest-time dedup keeps Fitbit-only walks as activity days, but those NEAT walks
must not inflate "cardio minutes" / overload lights. This module is pure and
safe to call at query time over already-loaded ``cardio_events``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pipeline.apple_health_cardio_dedup import (
    activity_family,
    apple_cardio_rows_to_drop,
)
from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.power_cardio_dedup import near_duplicate_power_cardio
from pipeline.power_source_priority import power_cardio_source_rank
from pipeline.workout_calendar import is_fitbit_origin_activity


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


def _power_richness(row: Mapping[str, Any]) -> int:
    score = 0
    if row.get("avg_watts") is not None or row.get("power_mmp_json"):
        score += 5
    if row.get("distance_miles") is not None:
        score += 2
    if row.get("avg_hr") is not None:
        score += 1
    if row.get("duration_min") is not None:
        score += 1
    return score


def _power_resolution_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        power_cardio_source_rank(row.get("source")),
        _power_richness(row),
        str(row.get("source_id") or ""),
    )


def _dedupe_cross_source_power(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep highest-priority row per near-duplicate power/cardio cluster."""
    if len(rows) < 2:
        return rows
    kept: list[dict[str, Any]] = []
    for row in sorted(rows, key=_power_resolution_key, reverse=True):
        if any(near_duplicate_power_cardio(row, k) for k in kept):
            continue
        kept.append(row)
    return kept


def filter_cardio_for_training_load(
    cardio_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop NEAT walks + strength mirrors, then collapse near-duplicate sessions.

    Apple Health hub dups (NRC/Strava/Fitbit mirrors) are resolved with the same
    rules as ingest cleanup. Cross-source power dups (Wahoo FIT vs Apple/Strava)
    are collapsed so minutes are not double-counted at query time even when the
    DB still holds historical duplicates.
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
    merged = apple + other
    return _dedupe_cross_source_power(merged)

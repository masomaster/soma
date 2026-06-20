"""Near-duplicate ``cardio_events`` from the Apple Health hub ingest path.

Health Sync (Google Fit / Fitbit → Apple Health) and native Watch / phone sources
can produce **multiple HealthKit workouts** for the same session with different
UUIDs. ``ON CONFLICT (user_id, source_id)`` does not catch those.

We drop incoming ``apple_health`` rows that **near-match** another row in the same
POST batch or an existing DB row on the same calendar day (activity type +
duration ±5 min; distance ±0.15 mi when both sides have distance). The richer row
(more GPS/HR fields) wins within a batch; existing DB rows win over weaker incoming
duplicates.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pipeline.features import as_date

logger = logging.getLogger(__name__)

_DURATION_TOL_MIN = 5.0
_DISTANCE_TOL_MI = 0.15


def _normalize_activity_type(raw: Any) -> str:
    if not isinstance(raw, str):
        return "workout"
    return " ".join(raw.strip().lower().split())


def _row_richness(row: dict[str, Any]) -> int:
    score = 0
    if row.get("distance_miles") is not None:
        score += 2
    if row.get("avg_hr") is not None:
        score += 1
    if row.get("max_hr") is not None:
        score += 1
    if row.get("calories") is not None:
        score += 1
    if row.get("elevation_ft") is not None:
        score += 1
    return score


def near_duplicate_apple_cardio(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two ``apple_health`` rows likely describe the same workout."""
    if a.get("source") != "apple_health" or b.get("source") != "apple_health":
        return False
    da = as_date(a.get("event_date"))
    db = as_date(b.get("event_date"))
    if da is None or da != db:
        return False
    if _normalize_activity_type(a.get("activity_type")) != _normalize_activity_type(
        b.get("activity_type")
    ):
        return False
    try:
        dur_a = float(a.get("duration_min") or 0)
        dur_b = float(b.get("duration_min") or 0)
    except (TypeError, ValueError):
        return False
    if abs(dur_a - dur_b) > _DURATION_TOL_MIN:
        return False
    dist_a = a.get("distance_miles")
    dist_b = b.get("distance_miles")
    if dist_a is not None and dist_b is not None:
        try:
            if abs(float(dist_a) - float(dist_b)) > _DISTANCE_TOL_MI:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _dedupe_batch(cardio_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the richest row when near-duplicates appear in one webhook POST."""
    if len(cardio_rows) < 2:
        return cardio_rows, 0
    ordered = sorted(
        cardio_rows,
        key=lambda r: (_row_richness(r), str(r.get("source_id") or "")),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in ordered:
        if any(near_duplicate_apple_cardio(row, k) for k in kept):
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


def _load_existing_apple_cardio(
    cur: Any,
    *,
    user_id: str,
    dates: list[date],
) -> list[dict[str, Any]]:
    if not dates:
        return []
    cur.execute(
        """
        SELECT source, source_id, event_date, activity_type, duration_min,
               distance_miles, elevation_ft, avg_hr, max_hr, avg_pace_sec_mi,
               calories, effort_zone, session_rpe, notes
        FROM cardio_events
        WHERE user_id = %s::uuid
          AND source = %s
          AND event_date = ANY(%s::date[])
        """,
        (user_id, "apple_health", dates),
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
        "avg_pace_sec_mi",
        "calories",
        "effort_zone",
        "session_rpe",
        "notes",
    )
    out: list[dict[str, Any]] = []
    for row in cur.fetchall():
        out.append(dict(zip(cols, row, strict=True)))
    return out


def filter_near_duplicate_apple_cardio(
    cur: Any,
    *,
    user_id: str,
    cardio_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Remove near-duplicate ``apple_health`` cardio before upsert.

    Returns ``(filtered_rows, dropped_count)``.
    """
    if not cardio_rows:
        return cardio_rows, 0

    batch_kept, batch_dropped = _dedupe_batch(cardio_rows)
    if not batch_kept:
        return batch_kept, batch_dropped

    day_set: set[date] = set()
    for r in batch_kept:
        d = as_date(r.get("event_date"))
        if d is not None:
            day_set.add(d)
    existing = _load_existing_apple_cardio(cur, user_id=user_id, dates=sorted(day_set))

    final: list[dict[str, Any]] = []
    db_dropped = 0
    for row in batch_kept:
        if any(near_duplicate_apple_cardio(row, ex) for ex in existing):
            db_dropped += 1
            continue
        final.append(row)

    total_dropped = batch_dropped + db_dropped
    if total_dropped:
        logger.info(
            "Apple Health hub dedup: dropped %d near-duplicate cardio row(s) "
            "(batch=%d, existing_db=%d) for user %s",
            total_dropped,
            batch_dropped,
            db_dropped,
            user_id,
        )
    return final, total_dropped

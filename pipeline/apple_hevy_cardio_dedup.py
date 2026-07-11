"""Drop Apple Health strength-style ``cardio_events`` when Hevy already has sets that day.

Hevy syncs logged sessions into HealthKit as workouts (often *Traditional Strength
Training*). Those become ``cardio_events`` with ``source = apple_health`` while the
same session is already represented as ``strength_events`` with ``source = hevy``.
This module removes those duplicates **per calendar day** when Hevy has any strength
rows on that day, matching activity types in
:data:`pipeline.cardio_quality.STRENGTH_LIKE_CARDIO_ACTIVITIES`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.features import as_date

logger = logging.getLogger(__name__)


def is_apple_strength_cardio_hevy_dup_candidate(row: dict[str, Any]) -> bool:
    """True if this row is Apple Health cardio that likely duplicates Hevy strength."""
    if row.get("source") != "apple_health":
        return False
    return is_strength_like_cardio_activity(row.get("activity_type"))


def _event_dates_for_hevy_on_days(
    cur: Any,
    *,
    user_id: str,
    dates: list[date],
) -> set[date]:
    """Return which of ``dates`` have at least one ``strength_events`` row from Hevy."""
    if not dates:
        return set()
    cur.execute(
        """
        SELECT DISTINCT event_date
        FROM strength_events
        WHERE user_id = %s::uuid
          AND source = %s
          AND event_date = ANY(%s::date[])
        """,
        (user_id, "hevy", dates),
    )
    rows = cur.fetchall()
    out: set[date] = set()
    for r in rows:
        if r and r[0] is not None:
            out.add(r[0])
    return out


def filter_apple_strength_cardio_when_hevy_present(
    cur: Any,
    *,
    user_id: str,
    cardio_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Remove strength-like Apple ``cardio_events`` on days with Hevy ``strength_events``.

    Returns ``(filtered_rows, dropped_count)``.
    """
    if not cardio_rows:
        return cardio_rows, 0

    candidates = [r for r in cardio_rows if is_apple_strength_cardio_hevy_dup_candidate(r)]
    if not candidates:
        return cardio_rows, 0

    day_set: set[date] = set()
    for r in candidates:
        d = as_date(r.get("event_date"))
        if d is not None:
            day_set.add(d)
    if not day_set:
        return cardio_rows, 0

    hevy_days = _event_dates_for_hevy_on_days(cur, user_id=user_id, dates=sorted(day_set))
    if not hevy_days:
        return cardio_rows, 0

    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in cardio_rows:
        ed = as_date(row.get("event_date"))
        if is_apple_strength_cardio_hevy_dup_candidate(row) and ed is not None and ed in hevy_days:
            dropped += 1
            continue
        kept.append(row)

    if dropped:
        logger.info(
            "Hevy/Apple dedup: dropped %d apple_health strength cardio row(s) for user %s (Hevy sets on same calendar day)",
            dropped,
            user_id,
        )
    return kept, dropped

"""Batch insert cardio_events with Postgres deduplication."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_CARDIO_COLUMNS: tuple[str, ...] = (
    "user_id",
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


def upsert_cardio_events(cur: Any, rows: list[dict[str, Any]]) -> None:
    """Insert normalized rows; duplicates skip via ``ON CONFLICT DO NOTHING``.

    Expects a psycopg2 cursor from a connection using the **service_role** (or
    any role allowed to write ``cardio_events``). RLS is bypassed for the
    service role — callers must attach the correct ``user_id`` per row.
    """
    if not rows:
        return
    for row in rows:
        missing = [c for c in _CARDIO_COLUMNS if c not in row]
        if missing:
            raise KeyError(f"cardio_events row missing keys: {missing}")
    values = [tuple(row[c] for c in _CARDIO_COLUMNS) for row in rows]
    col_sql = ", ".join(_CARDIO_COLUMNS)
    sql = (
        f"INSERT INTO cardio_events ({col_sql}) VALUES %s "
        "ON CONFLICT (user_id, source_id) DO NOTHING"
    )
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Upserted %s cardio_events row(s)", len(rows))

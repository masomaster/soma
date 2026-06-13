"""Batch insert strength_events with Postgres deduplication."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_STRENGTH_COLUMNS: tuple[str, ...] = (
    "user_id",
    "source",
    "source_id",
    "event_date",
    "exercise_name",
    "muscle_group",
    "movement_type",
    "superset_id",
    "set_number",
    "reps",
    "weight_lbs",
    "rpe",
    "set_type",
    "notes",
)


def upsert_strength_events(cur: Any, rows: list[dict[str, Any]]) -> None:
    """Insert normalized rows; duplicates skip via ``ON CONFLICT DO NOTHING``.

    Expects a psycopg2 cursor from a connection using the **service_role** (or
    any role allowed to write ``strength_events``). RLS is bypassed for the
    service role — callers must attach the correct ``user_id`` per row.
    """
    if not rows:
        return
    for row in rows:
        missing = [c for c in _STRENGTH_COLUMNS if c not in row]
        if missing:
            raise KeyError(f"strength_events row missing keys: {missing}")
    values = [tuple(row[c] for c in _STRENGTH_COLUMNS) for row in rows]
    col_sql = ", ".join(_STRENGTH_COLUMNS)
    sql = (
        f"INSERT INTO strength_events ({col_sql}) VALUES %s "
        "ON CONFLICT (user_id, source_id) DO NOTHING"
    )
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Upserted %s strength_events row(s)", len(rows))

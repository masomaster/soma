"""Batch insert cardio_events with Postgres deduplication."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_CARDIO_COLUMNS: tuple[str, ...] = (
    "user_id",
    "source",
    "source_app",
    "source_id",
    "event_date",
    "started_at",
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
    "quality_flags",
)

# Columns tolerated as absent on an incoming row (defaulted to NULL). Additive
# fields so older producers (e.g. the source_app backfill) keep working.
_OPTIONAL_CARDIO_COLUMNS: frozenset[str] = frozenset({"quality_flags"})


def upsert_cardio_events(cur: Any, rows: list[dict[str, Any]]) -> None:
    """Insert normalized rows; duplicates skip via ``ON CONFLICT DO NOTHING``.

    Expects a psycopg2 cursor from a connection using the **service_role** (or
    any role allowed to write ``cardio_events``). RLS is bypassed for the
    service role — callers must attach the correct ``user_id`` per row.
    """
    if not rows:
        return
    for row in rows:
        missing = [
            c for c in _CARDIO_COLUMNS if c not in row and c not in _OPTIONAL_CARDIO_COLUMNS
        ]
        if missing:
            raise KeyError(f"cardio_events row missing keys: {missing}")
    values = [tuple(row.get(c) for c in _CARDIO_COLUMNS) for row in rows]
    col_sql = ", ".join(_CARDIO_COLUMNS)
    sql = (
        f"INSERT INTO cardio_events ({col_sql}) VALUES %s "
        "ON CONFLICT (user_id, source_id) DO NOTHING"
    )
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Upserted %s cardio_events row(s)", len(rows))


def delete_cardio_events_by_source_id(
    cur: Any, *, user_id: str, source_ids: list[str]
) -> int:
    """Delete ``cardio_events`` rows for ``user_id`` matching ``source_ids``.

    Used when a higher-priority incoming duplicate supersedes rows already stored
    (e.g. a Nike Run Club run replacing a previously stored Fitbit copy). Returns
    the number of rows deleted.
    """
    if not source_ids:
        return 0
    cur.execute(
        "DELETE FROM cardio_events WHERE user_id = %s::uuid AND source_id = ANY(%s)",
        (user_id, source_ids),
    )
    deleted = max(cur.rowcount or 0, 0)
    if deleted:
        logger.info("Deleted %s superseded cardio_events row(s) for user %s", deleted, user_id)
    return deleted

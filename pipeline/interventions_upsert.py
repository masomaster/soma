"""Batch insert ``interventions`` rows (CalDAV / manual life events)."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_COLUMNS: tuple[str, ...] = (
    "user_id",
    "event_date",
    "category",
    "description",
    "is_ongoing",
    "end_date",
    "notes",
)


def insert_interventions(cur: Any, rows: list[dict[str, Any]]) -> None:
    """Insert intervention rows (no dedup — use ``replace_caldav_interventions`` for CalDAV sync)."""
    if not rows:
        return
    for row in rows:
        missing = [c for c in _COLUMNS if c not in row]
        if missing:
            raise KeyError(f"interventions row missing keys: {missing}")
    values = [tuple(row[c] for c in _COLUMNS) for row in rows]
    col_sql = ", ".join(_COLUMNS)
    sql = f"INSERT INTO interventions ({col_sql}) VALUES %s"
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Inserted %s intervention row(s)", len(rows))


def replace_caldav_interventions(cur: Any, *, user_id: str, rows: list[dict[str, Any]]) -> None:
    """Replace CalDAV-sourced rows for touched dates, then insert fresh snapshot."""
    from pipeline.adapters.caldav_calendar import CALDAV_SOURCE

    if not rows:
        return
    dates = list({r["event_date"] for r in rows})
    cur.execute(
        "DELETE FROM interventions WHERE user_id = %s AND notes = %s AND event_date = ANY(%s)",
        (user_id, CALDAV_SOURCE, dates),
    )
    insert_interventions(cur, rows)


# Back-compat alias
upsert_interventions = insert_interventions

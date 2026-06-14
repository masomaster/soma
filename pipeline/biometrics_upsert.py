"""Batch upsert ``biometrics`` rows (webhook / export ingest).

Conflict target matches ``0001_initial.sql``:
``UNIQUE (user_id, source, event_date, metric)``. Uses ``DO UPDATE`` so a
re-sent daily rollup or corrected export overwrites ``value`` / ``unit`` /
``raw_s3_key`` idempotently (unlike append-only ``strength_events``).
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

_BIOMETRICS_COLUMNS: tuple[str, ...] = (
    "user_id",
    "source",
    "event_date",
    "metric",
    "value",
    "unit",
    "raw_s3_key",
)


def upsert_biometrics(cur: Any, rows: list[dict[str, Any]]) -> None:
    """Insert or update normalized biometric rows.

    Expects a psycopg2 cursor on a **service_role** connection; callers must set
    ``user_id`` correctly on every row (RLS bypassed).
    """
    if not rows:
        return
    for row in rows:
        missing = [c for c in _BIOMETRICS_COLUMNS if c not in row]
        if missing:
            raise KeyError(f"biometrics row missing keys: {missing}")
    values = [tuple(row[c] for c in _BIOMETRICS_COLUMNS) for row in rows]
    col_sql = ", ".join(_BIOMETRICS_COLUMNS)
    sql = (
        f"INSERT INTO biometrics ({col_sql}) VALUES %s "
        "ON CONFLICT (user_id, source, event_date, metric) DO UPDATE SET "
        "value = EXCLUDED.value, "
        "unit = EXCLUDED.unit, "
        "raw_s3_key = EXCLUDED.raw_s3_key"
    )
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Upserted %s biometrics row(s)", len(rows))

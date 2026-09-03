"""Batch upsert ``biometrics`` rows (webhook / export ingest).

Conflict target matches ``0001_initial.sql``:
``UNIQUE (user_id, source, event_date, metric)``. Uses ``DO UPDATE`` so a
re-sent daily rollup or corrected export overwrites ``value`` / ``unit`` /
``raw_s3_key`` idempotently (unlike append-only ``strength_events``).

**Sleep metrics** (``sleep_hours``, ``sleep_deep_hrs``, ``sleep_rem_hrs``,
``sleep_score``) keep ``GREATEST(existing, incoming)`` on conflict. Health Auto
Export / Google Health often re-posts the same calendar date later with a short
morning fragment; last-write-wins would replace a full night with ~1–2h.
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

# Prefer the longer / higher sample when the same day is re-posted.
_SLEEP_KEEP_MAX_METRICS: frozenset[str] = frozenset(
    {"sleep_hours", "sleep_deep_hrs", "sleep_rem_hrs", "sleep_score"}
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
    sleep_list = ", ".join(f"'{m}'" for m in sorted(_SLEEP_KEEP_MAX_METRICS))
    sql = (
        f"INSERT INTO biometrics ({col_sql}) VALUES %s "
        "ON CONFLICT (user_id, source, event_date, metric) DO UPDATE SET "
        f"value = CASE WHEN EXCLUDED.metric IN ({sleep_list}) "
        "THEN GREATEST(biometrics.value, EXCLUDED.value) "
        "ELSE EXCLUDED.value END, "
        "unit = EXCLUDED.unit, "
        "raw_s3_key = CASE "
        f"WHEN EXCLUDED.metric IN ({sleep_list}) "
        "AND biometrics.value > EXCLUDED.value "
        "THEN biometrics.raw_s3_key ELSE EXCLUDED.raw_s3_key END"
    )
    execute_values(cur, sql, values, page_size=len(values))
    logger.debug("Upserted %s biometrics row(s)", len(rows))

"""Scheduled CalDAV poll: ``interventions`` upsert for busy blocks."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import psycopg2

from pipeline.adapters.caldav_calendar import normalize_caldav_events
from pipeline.interventions_upsert import replace_caldav_interventions
from pipeline.raw_storage import format_raw_object_key

logger = logging.getLogger(__name__)

CALDAV_RAW_SOURCE = "caldav_icloud"


def run_caldav_scheduled_ingest(
    *,
    user_id: str,
    dsn: str,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    fetch_events: Callable[[], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    events = list(fetch_events())
    key = format_raw_object_key(user_id, CALDAV_RAW_SOURCE, utc_now)
    raw_put(key, json.dumps(events, separators=(",", ":"), default=str).encode("utf-8"))
    rows = normalize_caldav_events(events, user_id)
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                replace_caldav_interventions(cur, user_id=user_id, rows=rows)
    finally:
        conn.close()
    logger.info("CalDAV ingest ok user=%s intervention_rows=%d", user_id, len(rows))
    return {"ok": True, "intervention_rows": len(rows)}

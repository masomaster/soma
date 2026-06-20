"""Scheduled Strava pull: raw S3 + ``cardio_events`` upsert (Phase 7 slice).

Live OAuth refresh is deferred until Strava subscription unpauses; this module
is wired in CDK with ``schedule_enabled=False`` by default on prod.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psycopg2

from pipeline.adapters.strava import fetch_and_normalize_from_api
from pipeline.cardio_upsert import upsert_cardio_events

logger = logging.getLogger(__name__)


def run_strava_scheduled_ingest(
    *,
    user_id: str,
    access_token: str,
    dsn: str,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    fetch_normalize: Callable[..., list[dict[str, Any]]] = fetch_and_normalize_from_api,
) -> dict[str, Any]:
    rows = fetch_normalize(user_id, access_token, raw_put=raw_put, utc_now=utc_now)
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                upsert_cardio_events(cur, rows)
    finally:
        conn.close()
    logger.info("Strava scheduled ingest ok user=%s cardio_rows=%d", user_id, len(rows))
    return {"ok": True, "cardio_rows": len(rows)}

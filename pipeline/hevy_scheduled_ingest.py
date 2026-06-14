"""Scheduled Hevy pull: raw S3 (via ``raw_put``) + ``strength_events`` upsert."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psycopg2

from pipeline.adapters.hevy import fetch_and_normalize_from_api
from pipeline.strength_upsert import upsert_strength_events

logger = logging.getLogger(__name__)


def run_hevy_scheduled_ingest(
    *,
    user_id: str,
    api_key: str,
    dsn: str,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    fetch_normalize: Callable[..., list[dict[str, Any]]] = fetch_and_normalize_from_api,
) -> dict[str, Any]:
    """Fetch all Hevy workout pages, write each page raw, upsert normalized rows.

    ``fetch_normalize`` defaults to :func:`pipeline.adapters.hevy.fetch_and_normalize_from_api`;
    tests inject a stub that avoids HTTP.
    """
    rows = fetch_normalize(user_id, api_key, raw_put=raw_put, utc_now=utc_now)
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                upsert_strength_events(cur, rows)
    finally:
        conn.close()
    logger.info("Hevy scheduled ingest ok user=%s strength_rows=%d", user_id, len(rows))
    return {"ok": True, "strength_rows": len(rows)}

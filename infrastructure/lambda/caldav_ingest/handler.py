"""EventBridge Scheduler → CalDAV poll → ``interventions`` busy blocks."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import boto3

from pipeline.adapters.caldav_calendar import fetch_caldav_events
from pipeline.caldav_scheduled_ingest import run_caldav_scheduled_ingest
from pipeline.lambda_secrets import resolve_caldav_credentials, resolve_db_connect_string, resolve_soma_user_id

logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _fetch_caldav_events() -> list[dict[str, Any]]:
    url, username, password = resolve_caldav_credentials()
    calendar_name = os.environ.get("CALDAV_CALENDAR_NAME", "").strip() or None
    start = date.today() - timedelta(days=7)
    end = date.today() + timedelta(days=30)
    return fetch_caldav_events(
        url=url,
        username=username,
        password=password,
        start=start,
        end=end,
        calendar_name=calendar_name,
    )


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    bucket = os.environ.get("RAW_BUCKET", "").strip()
    if not bucket:
        raise OSError("RAW_BUCKET is not configured")

    user_id = resolve_soma_user_id()
    dsn = resolve_db_connect_string()
    s3 = boto3.client("s3")

    def raw_put(key: str, body: bytes) -> None:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    result = run_caldav_scheduled_ingest(
        user_id=user_id,
        dsn=dsn,
        raw_put=raw_put,
        utc_now=datetime.now(timezone.utc),
        fetch_events=_fetch_caldav_events,
    )
    logger.info("CalDAV ingest Lambda finished %s", result)
    return result

"""EventBridge Scheduler → Strava API → raw S3 → ``cardio_events`` (paused live by default)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from pipeline.lambda_secrets import resolve_db_connect_string, resolve_soma_user_id, resolve_strava_access_token
from pipeline.strava_scheduled_ingest import run_strava_scheduled_ingest

logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    bucket = os.environ.get("RAW_BUCKET", "").strip()
    if not bucket:
        raise OSError("RAW_BUCKET is not configured")

    user_id = resolve_soma_user_id()
    token = resolve_strava_access_token()
    dsn = resolve_db_connect_string()
    s3 = boto3.client("s3")

    def raw_put(key: str, body: bytes) -> None:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    result = run_strava_scheduled_ingest(
        user_id=user_id,
        access_token=token,
        dsn=dsn,
        raw_put=raw_put,
        utc_now=datetime.now(timezone.utc),
    )
    logger.info("Strava ingest Lambda finished %s", result)
    return result

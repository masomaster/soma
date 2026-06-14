"""EventBridge Scheduler → Hevy API → raw S3 → ``strength_events`` upsert.

Environment (set by CDK):

    ENV                     staging|prod
    SOMA_LAMBDA_SECRET_ARN  Secrets Manager JSON with ``DB_CONNECT_STRING``,
                            ``HEVY_API_KEY``, ``SOMA_USER_ID`` (or set those as env)
    RAW_BUCKET              Same S3 bucket as Apple Health webhook (``raw/{user_id}/...``)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from pipeline.hevy_scheduled_ingest import run_hevy_scheduled_ingest
from pipeline.lambda_secrets import (
    resolve_db_connect_string,
    resolve_hevy_api_key,
    resolve_soma_user_id,
)

logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    bucket = os.environ.get("RAW_BUCKET", "").strip()
    if not bucket:
        raise OSError("RAW_BUCKET is not configured")

    user_id = resolve_soma_user_id()
    api_key = resolve_hevy_api_key()
    dsn = resolve_db_connect_string()

    s3_client = boto3.client("s3")

    def raw_put(key: str, body: bytes) -> None:
        s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    utc = datetime.now(timezone.utc)
    result = run_hevy_scheduled_ingest(
        user_id=user_id,
        api_key=api_key,
        dsn=dsn,
        raw_put=raw_put,
        utc_now=utc,
    )
    logger.info("Hevy ingest Lambda finished %s", result)
    return result

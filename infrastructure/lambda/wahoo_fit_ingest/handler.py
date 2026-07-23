"""EventBridge Scheduler → Dropbox API → raw S3 → ``cardio_events`` + FTP."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from pipeline.lambda_secrets import (
    resolve_db_connect_string,
    resolve_dropbox_credentials,
    resolve_soma_user_id,
)
from pipeline.wahoo_fit_scheduled_ingest import run_wahoo_fit_dropbox_ingest

logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    bucket = os.environ.get("RAW_BUCKET", "").strip()
    if not bucket:
        raise OSError("RAW_BUCKET is not configured")

    user_id = resolve_soma_user_id()
    app_key, app_secret, refresh_token, folder_path = resolve_dropbox_credentials()
    dsn = resolve_db_connect_string()
    s3 = boto3.client("s3")

    def raw_put(key: str, body: bytes) -> None:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    result = run_wahoo_fit_dropbox_ingest(
        user_id=user_id,
        app_key=app_key,
        app_secret=app_secret,
        refresh_token=refresh_token,
        folder_path=folder_path,
        dsn=dsn,
        raw_put=raw_put,
        utc_now=datetime.now(timezone.utc),
        estimate_ftp=True,
    )
    logger.info("Wahoo FIT Dropbox ingest Lambda finished %s", result)
    return result

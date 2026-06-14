"""HTTP API (API Gateway v2) → raw S3 → ``biometrics`` + ``cardio_events`` upsert.

Environment (set by CDK):

    ENV                     staging|prod
    SOMA_LAMBDA_SECRET_ARN  Secrets Manager JSON with at least ``DB_CONNECT_STRING``
    RAW_BUCKET              S3 bucket for ``raw/{user_id}/...`` JSON

    Webhook auth (optional): header ``X-Soma-Webhook-Secret`` must match when a
    non-placeholder secret is configured. Source order (see
    ``pipeline.lambda_secrets.resolve_apple_health_webhook_secret_optional``):

    1. Lambda env ``APPLE_HEALTH_WEBHOOK_SECRET`` (non-``update_me``), or
    2. JSON key ``APPLE_HEALTH_WEBHOOK_SECRET`` on the same Secrets Manager secret
       as ``DB_CONNECT_STRING`` (``SOMA_LAMBDA_SECRET_ARN``). Value ``update_me``
       or empty means **disabled** until you set a real string.

Required HTTP headers on each **POST**:

    X-Soma-User-Id   Supabase ``auth.users.id`` UUID for the tenant row (service role upsert).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import boto3
import psycopg2

from pipeline.adapters.apple_health_export import ingest_apple_health_payload_complete
from pipeline.apple_hevy_cardio_dedup import filter_apple_strength_cardio_when_hevy_present
from pipeline.apple_health_webhook_event import (
    HINT_EMPTY_BODY,
    HINT_INVALID_JSON,
    HINT_MISSING_USER,
    header_first,
    merge_api_gateway_headers,
    parse_json_body,
    raw_body_bytes,
)
from pipeline.biometrics_upsert import upsert_biometrics
from pipeline.cardio_upsert import upsert_cardio_events
from pipeline.lambda_secrets import (
    resolve_apple_health_webhook_secret_optional,
    resolve_db_connect_string,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _bad_request(error: str, hint: str) -> dict[str, Any]:
    logger.warning("Apple Health webhook 400 error=%s", error)
    return _response(400, {"ok": False, "error": error, "hint": hint})


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    method = (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", event.get("httpMethod", "GET"))
    )
    if str(method).upper() != "POST":
        return _response(405, {"ok": False, "error": "method_not_allowed"})

    headers = merge_api_gateway_headers(event)

    expected = resolve_apple_health_webhook_secret_optional()
    if expected:
        got = header_first(headers, "x-soma-webhook-secret") or ""
        if not secrets.compare_digest(expected, got):
            logger.warning("Webhook secret mismatch or missing")
            return _response(401, {"ok": False, "error": "unauthorized"})

    user_id = header_first(headers, "x-soma-user-id")
    if not user_id:
        return _bad_request("missing_header_x_soma_user_id", HINT_MISSING_USER)

    raw_bytes = raw_body_bytes(event)
    logger.info(
        "Apple Health webhook POST body_bytes=%d header_keys=%s",
        len(raw_bytes),
        sorted(str(k).lower() for k in headers),
    )
    body_obj, parse_err = parse_json_body(raw_bytes)
    if parse_err == "empty_body":
        return _bad_request("empty_body", HINT_EMPTY_BODY)
    if parse_err == "invalid_utf8":
        logger.info("Invalid UTF-8 in request body")
        return _bad_request("invalid_utf8", HINT_INVALID_JSON)
    if parse_err == "invalid_json":
        logger.info("Invalid JSON in request body")
        return _bad_request("invalid_json", HINT_INVALID_JSON)
    assert body_obj is not None

    bucket = os.environ.get("RAW_BUCKET", "").strip()
    if not bucket:
        return _response(500, {"ok": False, "error": "raw_bucket_not_configured"})

    s3c = boto3.client("s3")

    def raw_put(key: str, body: bytes) -> None:
        s3c.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    utc = datetime.now(timezone.utc)
    try:
        _key, bio_rows, cardio_rows = ingest_apple_health_payload_complete(
            user_id, body_obj, raw_put=raw_put, utc_now=utc
        )
    except Exception as exc:
        logger.exception("Normalize/ingest failed: %s", exc)
        return _response(500, {"ok": False, "error": type(exc).__name__})

    try:
        dsn = resolve_db_connect_string()
    except OSError as exc:
        logger.error("DB configuration error: %s", exc)
        return _response(500, {"ok": False, "error": "db_config"})

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                upsert_biometrics(cur, bio_rows)
                cardio_for_db, cardio_dropped_hevy = filter_apple_strength_cardio_when_hevy_present(
                    cur, user_id=user_id, cardio_rows=cardio_rows
                )
                upsert_cardio_events(cur, cardio_for_db)
    except Exception as exc:
        logger.exception("Postgres upsert failed: %s", exc)
        return _response(500, {"ok": False, "error": "database"})
    finally:
        conn.close()

    logger.info(
        "Apple Health webhook ok user=%s biometrics=%d cardio=%d (dropped_hevy_dup=%d)",
        user_id,
        len(bio_rows),
        len(cardio_for_db),
        cardio_dropped_hevy,
    )
    return _response(
        200,
        {
            "ok": True,
            "biometrics_upserted": len(bio_rows),
            "cardio_events_upserted": len(cardio_for_db),
            "cardio_events_dropped_hevy_strength_dup": cardio_dropped_hevy,
        },
    )

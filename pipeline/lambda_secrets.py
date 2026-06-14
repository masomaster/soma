"""Resolve DB / Anthropic / SES settings for the briefing Lambda.

Plain environment variables are used when all three are set (local dev and
tests). In AWS, CDK sets ``SOMA_LAMBDA_SECRET_ARN`` to a Secrets Manager secret
whose ``SecretString`` is JSON with keys ``DB_CONNECT_STRING``, ``ANTHROPIC_API_KEY``,
``SES_SENDER``, and optionally ``APPLE_HEALTH_WEBHOOK_SECRET`` (see
:func:`resolve_apple_health_webhook_secret_optional`), ``HEVY_API_KEY``, and
``SOMA_USER_ID`` (scheduled Hevy ingest; see :func:`resolve_hevy_api_key` and
:func:`resolve_soma_user_id`).

Secrets Manager JSON is fetched at most **once per process per ARN** (see
:func:`clear_runtime_secret_json_cache` for tests).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any


def clear_runtime_secret_json_cache() -> None:
    """Clear the in-memory Secrets Manager JSON cache (call between tests if mocks change)."""
    _runtime_secret_json_raw.cache_clear()


@lru_cache(maxsize=8)
def _runtime_secret_json_raw(arn: str) -> str:
    import boto3

    sm = boto3.client("secretsmanager")
    return str(sm.get_secret_value(SecretId=arn)["SecretString"])


def _runtime_secret_dict(arn: str) -> dict[str, Any]:
    return json.loads(_runtime_secret_json_raw(arn))


def resolve_lambda_secrets() -> tuple[str, str, str]:
    """Return ``(db_connect_string, anthropic_api_key, ses_sender)``."""
    db = os.environ.get("DB_CONNECT_STRING", "").strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ses = os.environ.get("SES_SENDER", "").strip()
    if db and key and ses:
        return db, key, ses

    arn = os.environ.get("SOMA_LAMBDA_SECRET_ARN", "").strip()
    if not arn:
        msg = (
            "Missing secrets: set DB_CONNECT_STRING, ANTHROPIC_API_KEY, and SES_SENDER, "
            "or set SOMA_LAMBDA_SECRET_ARN to a Secrets Manager secret whose string is JSON "
            "with those three keys."
        )
        raise OSError(msg)

    data = _runtime_secret_dict(arn)
    db_v = str(data.get("DB_CONNECT_STRING", "")).strip()
    key_v = str(data.get("ANTHROPIC_API_KEY", "")).strip()
    ses_v = str(data.get("SES_SENDER", "")).strip()
    if not (db_v and key_v and ses_v):
        raise OSError(
            f"Secret {arn!r} must be JSON with non-empty "
            "DB_CONNECT_STRING, ANTHROPIC_API_KEY, SES_SENDER."
        )
    return db_v, key_v, ses_v


def resolve_db_connect_string() -> str:
    """Return Postgres URI for Lambdas that only need the database (e.g. ingest webhooks).

    Uses ``DB_CONNECT_STRING`` from the environment if set; otherwise reads the same
    Secrets Manager JSON as :func:`resolve_lambda_secrets` but **does not** require
    Anthropic or SES keys to be present.
    """
    db = os.environ.get("DB_CONNECT_STRING", "").strip()
    if db:
        return db

    arn = os.environ.get("SOMA_LAMBDA_SECRET_ARN", "").strip()
    if not arn:
        raise OSError(
            "Missing DB: set DB_CONNECT_STRING or SOMA_LAMBDA_SECRET_ARN with a JSON secret "
            "that includes DB_CONNECT_STRING."
        )

    data = _runtime_secret_dict(arn)
    db_v = str(data.get("DB_CONNECT_STRING", "")).strip()
    if not db_v:
        raise OSError(f"Secret {arn!r} must include non-empty DB_CONNECT_STRING.")
    return db_v


def _is_webhook_secret_placeholder(value: str) -> bool:
    s = value.strip().lower()
    return s in {"", "update_me"}


def resolve_apple_health_webhook_secret_optional() -> str:
    """Return the shared secret for ``X-Soma-Webhook-Secret``, or empty string to disable.

    Resolution order:

    1. Environment variable ``APPLE_HEALTH_WEBHOOK_SECRET`` if set and not a
       placeholder (``update_me`` / empty disables checks, same as unset).
    2. Else JSON key ``APPLE_HEALTH_WEBHOOK_SECRET`` on the same Secrets Manager
       secret as :func:`resolve_db_connect_string` (``SOMA_LAMBDA_SECRET_ARN``).

    Placeholder ``update_me`` (any case) is treated as **unset** so seeded
    secrets do not accidentally lock the webhook before you replace the value.
    """
    env = os.environ.get("APPLE_HEALTH_WEBHOOK_SECRET", "").strip()
    if env and not _is_webhook_secret_placeholder(env):
        return env

    arn = os.environ.get("SOMA_LAMBDA_SECRET_ARN", "").strip()
    if not arn:
        return ""

    data = _runtime_secret_dict(arn)
    v = str(data.get("APPLE_HEALTH_WEBHOOK_SECRET", "")).strip()
    if not v or _is_webhook_secret_placeholder(v):
        return ""
    return v


def resolve_hevy_api_key() -> str:
    """Return Hevy Pro ``api-key`` for scheduled ingest.

    Order: ``HEVY_API_KEY`` env if set and not a placeholder; else JSON key
    ``HEVY_API_KEY`` on ``SOMA_LAMBDA_SECRET_ARN``. Placeholder ``update_me`` /
    empty means unset.
    """
    env = os.environ.get("HEVY_API_KEY", "").strip()
    if env and not _is_webhook_secret_placeholder(env):
        return env

    arn = os.environ.get("SOMA_LAMBDA_SECRET_ARN", "").strip()
    if not arn:
        raise OSError(
            "Missing Hevy API key: set HEVY_API_KEY or SOMA_LAMBDA_SECRET_ARN with JSON "
            "that includes HEVY_API_KEY."
        )

    data = _runtime_secret_dict(arn)
    v = str(data.get("HEVY_API_KEY", "")).strip()
    if not v or _is_webhook_secret_placeholder(v):
        raise OSError(
            f"Secret {arn!r} must include non-empty HEVY_API_KEY (not update_me) for Hevy ingest."
        )
    return v


def resolve_soma_user_id() -> str:
    """Return Supabase ``auth.users.id`` UUID for the Hevy scheduled-ingest tenant.

    Order: ``SOMA_USER_ID`` env if set and not a placeholder; else JSON key
    ``SOMA_USER_ID`` on ``SOMA_LAMBDA_SECRET_ARN``.
    """
    env = os.environ.get("SOMA_USER_ID", "").strip()
    if env and not _is_webhook_secret_placeholder(env):
        return env

    arn = os.environ.get("SOMA_LAMBDA_SECRET_ARN", "").strip()
    if not arn:
        raise OSError(
            "Missing tenant user id: set SOMA_USER_ID or SOMA_LAMBDA_SECRET_ARN with JSON "
            "that includes SOMA_USER_ID."
        )

    data = _runtime_secret_dict(arn)
    v = str(data.get("SOMA_USER_ID", "")).strip()
    if not v or _is_webhook_secret_placeholder(v):
        raise OSError(
            f"Secret {arn!r} must include non-empty SOMA_USER_ID (not update_me) for Hevy ingest."
        )
    return v

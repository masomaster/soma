"""Resolve Lambda secrets from per-concern Secrets Manager ARNs or plain env vars.

AWS Lambdas receive env vars such as ``SOMA_DB_SECRET_ARN``, ``SOMA_BRIEFING_SECRET_ARN``,
``SOMA_HEVY_SECRET_ARN``, etc. (see :mod:`soma_cdk.runtime_secrets`). Local dev and
tests set ``DB_CONNECT_STRING``, ``ANTHROPIC_API_KEY``, … directly.

Each ARN is fetched at most once per process (LRU cache).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

ENV_SOMA_DB_SECRET_ARN = "SOMA_DB_SECRET_ARN"
ENV_SOMA_BRIEFING_SECRET_ARN = "SOMA_BRIEFING_SECRET_ARN"
ENV_SOMA_TENANT_SECRET_ARN = "SOMA_TENANT_SECRET_ARN"
ENV_SOMA_HEVY_SECRET_ARN = "SOMA_HEVY_SECRET_ARN"
ENV_SOMA_CALDAV_SECRET_ARN = "SOMA_CALDAV_SECRET_ARN"
ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN = "SOMA_APPLE_WEBHOOK_SECRET_ARN"
ENV_SOMA_STRAVA_SECRET_ARN = "SOMA_STRAVA_SECRET_ARN"


def clear_runtime_secret_json_cache() -> None:
    """Clear the in-memory Secrets Manager cache (call between tests if mocks change)."""
    _secret_string_raw.cache_clear()


@lru_cache(maxsize=16)
def _secret_string_raw(arn: str) -> str:
    import boto3

    sm = boto3.client("secretsmanager")
    return str(sm.get_secret_value(SecretId=arn)["SecretString"])


def _secret_json(arn: str) -> dict[str, Any]:
    data = json.loads(_secret_string_raw(arn))
    if not isinstance(data, dict):
        raise OSError(f"Secret {arn!r} must be a JSON object.")
    return data


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in {"", "update_me"}


def _require_arn(env_var: str) -> str:
    arn = os.environ.get(env_var, "").strip()
    if not arn:
        raise OSError(f"Missing {env_var}: set it to a Secrets Manager secret ARN.")
    return arn


def _plain_from_secret(*, env_var: str, arn_env: str, label: str, required: bool = True) -> str:
    direct = os.environ.get(env_var, "").strip()
    if direct and not _is_placeholder(direct):
        return direct
    arn = os.environ.get(arn_env, "").strip()
    if not arn:
        if required:
            raise OSError(f"Missing {label}: set {env_var} or {arn_env}.")
        return ""
    raw = _secret_string_raw(arn).strip()
    if _is_placeholder(raw):
        if required:
            raise OSError(f"Secret {arn!r} must contain a non-placeholder {label}.")
        return ""
    return raw


def _json_key_from_secret(*, arn_env: str, key: str, required: bool = True) -> str:
    arn = _require_arn(arn_env)
    data = _secret_json(arn)
    v = str(data.get(key, "")).strip()
    if not v or _is_placeholder(v):
        if required:
            raise OSError(f"Secret {arn!r} must include non-empty {key} (not update_me).")
        return ""
    return v


def resolve_db_connect_string() -> str:
    """Postgres URI from ``DB_CONNECT_STRING`` env or ``SOMA_DB_SECRET_ARN`` (plain string)."""
    return _plain_from_secret(
        env_var="DB_CONNECT_STRING",
        arn_env=ENV_SOMA_DB_SECRET_ARN,
        label="DB_CONNECT_STRING",
    )


def resolve_lambda_secrets() -> tuple[str, str, str]:
    """Return ``(db_connect_string, anthropic_api_key, ses_sender)`` for briefing / weekly LLM."""
    db = os.environ.get("DB_CONNECT_STRING", "").strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ses = os.environ.get("SES_SENDER", "").strip()
    if db and key and ses:
        return db, key, ses

    db_v = resolve_db_connect_string()
    anthropic = _json_key_from_secret(
        arn_env=ENV_SOMA_BRIEFING_SECRET_ARN, key="ANTHROPIC_API_KEY"
    )
    ses_v = _json_key_from_secret(arn_env=ENV_SOMA_BRIEFING_SECRET_ARN, key="SES_SENDER")
    return db_v, anthropic, ses_v


def resolve_apple_health_webhook_secret_optional() -> str:
    """Webhook HMAC from env, or plain ``SOMA_APPLE_WEBHOOK_SECRET_ARN``; empty disables auth."""
    env = os.environ.get("APPLE_HEALTH_WEBHOOK_SECRET", "").strip()
    if env and not _is_placeholder(env):
        return env
    return _plain_from_secret(
        env_var="APPLE_HEALTH_WEBHOOK_SECRET",
        arn_env=ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN,
        label="APPLE_HEALTH_WEBHOOK_SECRET",
        required=False,
    )


def resolve_hevy_api_key() -> str:
    """Hevy API key from ``HEVY_API_KEY`` or plain ``SOMA_HEVY_SECRET_ARN``."""
    return _plain_from_secret(
        env_var="HEVY_API_KEY",
        arn_env=ENV_SOMA_HEVY_SECRET_ARN,
        label="HEVY_API_KEY",
    )


def resolve_soma_user_id() -> str:
    """Tenant UUID from ``SOMA_USER_ID`` or plain ``SOMA_TENANT_SECRET_ARN``."""
    return _plain_from_secret(
        env_var="SOMA_USER_ID",
        arn_env=ENV_SOMA_TENANT_SECRET_ARN,
        label="SOMA_USER_ID",
    )


def resolve_strava_access_token() -> str:
    """Strava token from ``STRAVA_ACCESS_TOKEN`` or plain ``SOMA_STRAVA_SECRET_ARN``."""
    return _plain_from_secret(
        env_var="STRAVA_ACCESS_TOKEN",
        arn_env=ENV_SOMA_STRAVA_SECRET_ARN,
        label="STRAVA_ACCESS_TOKEN",
    )


def resolve_caldav_credentials() -> tuple[str, str, str]:
    """CalDAV triple from env vars or JSON ``SOMA_CALDAV_SECRET_ARN``."""
    url = os.environ.get("CALDAV_URL", "").strip()
    username = os.environ.get("CALDAV_USERNAME", "").strip()
    password = os.environ.get("CALDAV_PASSWORD", "").strip()
    if url and username and password and not any(_is_placeholder(v) for v in (url, username, password)):
        return url, username, password

    arn = _require_arn(ENV_SOMA_CALDAV_SECRET_ARN)
    data = _secret_json(arn)
    url_v = str(data.get("CALDAV_URL", "")).strip()
    user_v = str(data.get("CALDAV_USERNAME", "")).strip()
    pass_v = str(data.get("CALDAV_PASSWORD", "")).strip()
    if not url_v or _is_placeholder(url_v):
        raise OSError(f"Secret {arn!r} must include non-empty CALDAV_URL.")
    if not user_v or _is_placeholder(user_v):
        raise OSError(f"Secret {arn!r} must include non-empty CALDAV_USERNAME.")
    if not pass_v or _is_placeholder(pass_v):
        raise OSError(f"Secret {arn!r} must include non-empty CALDAV_PASSWORD.")
    return url_v, user_v, pass_v

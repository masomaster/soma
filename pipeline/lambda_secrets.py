"""Resolve DB / Anthropic / SES settings for the briefing Lambda.

Plain environment variables are used when all three are set (local dev and
tests). In AWS, CDK sets ``SOMA_LAMBDA_SECRET_ARN`` to a Secrets Manager secret
whose ``SecretString`` is JSON with keys ``DB_CONNECT_STRING``, ``ANTHROPIC_API_KEY``,
``SES_SENDER``.
"""

from __future__ import annotations

import json
import os


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

    import boto3

    sm = boto3.client("secretsmanager")
    raw = sm.get_secret_value(SecretId=arn)["SecretString"]
    data = json.loads(raw)
    db_v = str(data.get("DB_CONNECT_STRING", "")).strip()
    key_v = str(data.get("ANTHROPIC_API_KEY", "")).strip()
    ses_v = str(data.get("SES_SENDER", "")).strip()
    if not (db_v and key_v and ses_v):
        raise OSError(
            f"Secret {arn!r} must be JSON with non-empty "
            "DB_CONNECT_STRING, ANTHROPIC_API_KEY, SES_SENDER."
        )
    return db_v, key_v, ses_v

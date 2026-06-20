"""Tests for :mod:`pipeline.lambda_secrets`."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from pipeline.lambda_secrets import (
    ENV_SOMA_BRIEFING_SECRET_ARN,
    ENV_SOMA_DB_SECRET_ARN,
    clear_runtime_secret_json_cache,
    resolve_caldav_credentials,
    resolve_lambda_secrets,
    resolve_strava_access_token,
)


def test_resolve_prefers_plain_env_vars() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "DB_CONNECT_STRING": "postgres://x",
            "ANTHROPIC_API_KEY": "sk-ant",
            "SES_SENDER": "a@b.co",
            ENV_SOMA_DB_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:db",
        },
        clear=False,
    ):
        assert resolve_lambda_secrets() == ("postgres://x", "sk-ant", "a@b.co")


def test_resolve_from_split_secrets_manager() -> None:
    fake_sm = mock.MagicMock()

    def _get_secret(*, SecretId: str) -> dict[str, str]:
        if SecretId == "arn:aws:secretsmanager:us-west-2:123:secret:db":
            return {"SecretString": " postgres://y "}
        if SecretId == "arn:aws:secretsmanager:us-west-2:123:secret:briefing":
            return {
                "SecretString": json.dumps(
                    {"ANTHROPIC_API_KEY": "key2", "SES_SENDER": "c@d.co"}
                )
            }
        raise AssertionError(f"unexpected SecretId {SecretId!r}")

    fake_sm.get_secret_value.side_effect = _get_secret

    def _client(name: str, **_kwargs: object):
        assert name == "secretsmanager"
        return fake_sm

    env = {
        "DB_CONNECT_STRING": "",
        "ANTHROPIC_API_KEY": "",
        "SES_SENDER": "",
        ENV_SOMA_DB_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:123:secret:db",
        ENV_SOMA_BRIEFING_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:123:secret:briefing",
    }
    clear_runtime_secret_json_cache()
    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch("boto3.client", _client):
            db, key, ses = resolve_lambda_secrets()

    assert db == "postgres://y"
    assert key == "key2"
    assert ses == "c@d.co"
    assert fake_sm.get_secret_value.call_count == 2


def test_resolve_missing_raises() -> None:
    clear_runtime_secret_json_cache()
    with mock.patch.dict(
        os.environ,
        {
            "DB_CONNECT_STRING": "",
            "ANTHROPIC_API_KEY": "",
            "SES_SENDER": "",
            ENV_SOMA_DB_SECRET_ARN: "",
            ENV_SOMA_BRIEFING_SECRET_ARN: "",
        },
    ):
        with pytest.raises(OSError, match="Missing DB_CONNECT_STRING"):
            resolve_lambda_secrets()


def test_resolve_strava_access_token_from_env() -> None:
    with mock.patch.dict(os.environ, {"STRAVA_ACCESS_TOKEN": "tok"}, clear=False):
        assert resolve_strava_access_token() == "tok"


def test_resolve_caldav_credentials_from_env() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "CALDAV_URL": "https://caldav.icloud.com",
            "CALDAV_USERNAME": "user",
            "CALDAV_PASSWORD": "app-pass",
        },
        clear=False,
    ):
        assert resolve_caldav_credentials() == (
            "https://caldav.icloud.com",
            "user",
            "app-pass",
        )

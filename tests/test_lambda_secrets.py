"""Tests for :mod:`pipeline.lambda_secrets`."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from pipeline.lambda_secrets import resolve_lambda_secrets


def test_resolve_prefers_plain_env_vars() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "DB_CONNECT_STRING": "postgres://x",
            "ANTHROPIC_API_KEY": "sk-ant",
            "SES_SENDER": "a@b.co",
            "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:x",
        },
        clear=False,
    ):
        assert resolve_lambda_secrets() == ("postgres://x", "sk-ant", "a@b.co")


def test_resolve_from_secrets_manager_json() -> None:
    payload = json.dumps(
        {
            "DB_CONNECT_STRING": " postgres://y ",
            "ANTHROPIC_API_KEY": "key2",
            "SES_SENDER": "c@d.co",
        }
    )
    fake_sm = mock.MagicMock()
    fake_sm.get_secret_value.return_value = {"SecretString": payload}

    def _client(name: str, **_kwargs: object):
        assert name == "secretsmanager"
        return fake_sm

    env = {
        "DB_CONNECT_STRING": "",
        "ANTHROPIC_API_KEY": "",
        "SES_SENDER": "",
        "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:123:secret:test",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch("boto3.client", _client):
            db, key, ses = resolve_lambda_secrets()

    assert db == "postgres://y"
    assert key == "key2"
    assert ses == "c@d.co"
    fake_sm.get_secret_value.assert_called_once_with(
        SecretId="arn:aws:secretsmanager:us-west-2:123:secret:test"
    )


def test_resolve_missing_raises() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "DB_CONNECT_STRING": "",
            "ANTHROPIC_API_KEY": "",
            "SES_SENDER": "",
            "SOMA_LAMBDA_SECRET_ARN": "",
        },
    ):
        with pytest.raises(OSError, match="Missing secrets"):
            resolve_lambda_secrets()

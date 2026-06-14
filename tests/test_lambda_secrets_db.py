"""DB-only secret resolution for ingest Lambdas."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline import lambda_secrets


def test_resolve_db_connect_string_prefers_env() -> None:
    with patch.dict(
        "os.environ",
        {"DB_CONNECT_STRING": "postgresql://local/test", "SOMA_LAMBDA_SECRET_ARN": ""},
        clear=False,
    ):
        assert lambda_secrets.resolve_db_connect_string() == "postgresql://local/test"


def test_resolve_db_connect_string_from_secrets_manager_json() -> None:
    fake = {"DB_CONNECT_STRING": "postgresql://pooler/example", "ANTHROPIC_API_KEY": "x", "SES_SENDER": "y"}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"DB_CONNECT_STRING": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_db_connect_string()
    assert out == "postgresql://pooler/example"


def test_resolve_db_connect_string_missing_raises() -> None:
    with patch.dict("os.environ", {"DB_CONNECT_STRING": "", "SOMA_LAMBDA_SECRET_ARN": ""}, clear=False):
        with pytest.raises(OSError, match="Missing DB"):
            lambda_secrets.resolve_db_connect_string()


def test_resolve_apple_health_webhook_secret_from_env_prefers_env() -> None:
    mock_sm = MagicMock()
    with patch.dict(
        "os.environ",
        {
            "APPLE_HEALTH_WEBHOOK_SECRET": "from-env-secret",
            "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x",
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == "from-env-secret"
    mock_sm.get_secret_value.assert_not_called()


def test_resolve_apple_health_webhook_secret_from_sm_json() -> None:
    fake = {
        "DB_CONNECT_STRING": "postgresql://x",
        "APPLE_HEALTH_WEBHOOK_SECRET": "hunter2",
    }
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == "hunter2"


def test_resolve_apple_health_webhook_secret_update_me_disables() -> None:
    fake = {"DB_CONNECT_STRING": "postgresql://x", "APPLE_HEALTH_WEBHOOK_SECRET": "update_me"}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == ""


def test_resolve_apple_health_webhook_secret_missing_key_empty() -> None:
    fake = {"DB_CONNECT_STRING": "postgresql://x"}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == ""

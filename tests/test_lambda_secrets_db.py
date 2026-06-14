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


def test_runtime_secret_json_single_sm_fetch_for_db_and_webhook() -> None:
    """Same ARN: resolve DB string and webhook secret must not double-fetch SM."""
    fake = {
        "DB_CONNECT_STRING": "postgresql://pooler/example",
        "APPLE_HEALTH_WEBHOOK_SECRET": "hunter2",
    }
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    arn = "arn:aws:secretsmanager:us-west-2:1:secret:soma/y"
    with patch.dict(
        "os.environ",
        {
            "DB_CONNECT_STRING": "",
            "APPLE_HEALTH_WEBHOOK_SECRET": "",
            "SOMA_LAMBDA_SECRET_ARN": arn,
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            db = lambda_secrets.resolve_db_connect_string()
            wh = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert db == "postgresql://pooler/example"
    assert wh == "hunter2"
    mock_sm.get_secret_value.assert_called_once()


def test_resolve_hevy_api_key_prefers_env() -> None:
    mock_sm = MagicMock()
    with patch.dict(
        "os.environ",
        {
            "HEVY_API_KEY": "hk-from-env",
            "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x",
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_hevy_api_key()
    assert out == "hk-from-env"
    mock_sm.get_secret_value.assert_not_called()


def test_resolve_hevy_api_key_from_secret_json() -> None:
    fake = {"DB_CONNECT_STRING": "postgresql://x", "HEVY_API_KEY": "hk-sm"}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"HEVY_API_KEY": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_hevy_api_key()
    assert out == "hk-sm"


def test_resolve_hevy_api_key_missing_raises() -> None:
    with patch.dict("os.environ", {"HEVY_API_KEY": "", "SOMA_LAMBDA_SECRET_ARN": ""}, clear=False):
        with pytest.raises(OSError, match="Missing Hevy"):
            lambda_secrets.resolve_hevy_api_key()


def test_resolve_soma_user_id_from_secret_json() -> None:
    uid = "11111111-1111-1111-1111-111111111111"
    fake = {"DB_CONNECT_STRING": "postgresql://x", "SOMA_USER_ID": uid}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"SOMA_USER_ID": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_soma_user_id()
    assert out == uid


def test_resolve_soma_user_id_update_me_raises() -> None:
    fake = {"DB_CONNECT_STRING": "postgresql://x", "SOMA_USER_ID": "update_me"}
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(fake)}
    with patch.dict(
        "os.environ",
        {"SOMA_USER_ID": "", "SOMA_LAMBDA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:1:secret:soma/x"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            with pytest.raises(OSError, match="SOMA_USER_ID"):
                lambda_secrets.resolve_soma_user_id()

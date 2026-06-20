"""DB-only secret resolution for ingest Lambdas."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline import lambda_secrets
from pipeline.lambda_secrets import (
    ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN,
    ENV_SOMA_DB_SECRET_ARN,
    ENV_SOMA_HEVY_SECRET_ARN,
    ENV_SOMA_TENANT_SECRET_ARN,
    clear_runtime_secret_json_cache,
)


def test_resolve_db_connect_string_prefers_env() -> None:
    with patch.dict(
        "os.environ",
        {"DB_CONNECT_STRING": "postgresql://local/test", ENV_SOMA_DB_SECRET_ARN: ""},
        clear=False,
    ):
        assert lambda_secrets.resolve_db_connect_string() == "postgresql://local/test"


def test_resolve_db_connect_string_from_secrets_manager_plain() -> None:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "postgresql://pooler/example"}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"DB_CONNECT_STRING": "", ENV_SOMA_DB_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/db"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_db_connect_string()
    assert out == "postgresql://pooler/example"


def test_resolve_db_connect_string_missing_raises() -> None:
    clear_runtime_secret_json_cache()
    with patch.dict("os.environ", {"DB_CONNECT_STRING": "", ENV_SOMA_DB_SECRET_ARN: ""}, clear=False):
        with pytest.raises(OSError, match="Missing DB_CONNECT_STRING"):
            lambda_secrets.resolve_db_connect_string()


def test_resolve_apple_health_webhook_secret_from_env_prefers_env() -> None:
    mock_sm = MagicMock()
    with patch.dict(
        "os.environ",
        {
            "APPLE_HEALTH_WEBHOOK_SECRET": "from-env-secret",
            ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/apple",
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == "from-env-secret"
    mock_sm.get_secret_value.assert_not_called()


def test_resolve_apple_health_webhook_secret_from_sm_plain() -> None:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "hunter2"}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/apple"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == "hunter2"


def test_resolve_apple_health_webhook_secret_update_me_disables() -> None:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "update_me"}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/apple"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == ""


def test_resolve_apple_health_webhook_secret_missing_arn_empty() -> None:
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"APPLE_HEALTH_WEBHOOK_SECRET": "", ENV_SOMA_APPLE_WEBHOOK_SECRET_ARN: ""},
        clear=False,
    ):
        out = lambda_secrets.resolve_apple_health_webhook_secret_optional()
    assert out == ""


def test_plain_secret_cached_per_arn() -> None:
    """Same DB ARN: resolve_db_connect_string must not double-fetch SM."""
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "postgresql://pooler/example"}
    arn = "arn:aws:secretsmanager:us-west-2:1:secret:soma/db"
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {
            "DB_CONNECT_STRING": "",
            ENV_SOMA_DB_SECRET_ARN: arn,
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            db1 = lambda_secrets.resolve_db_connect_string()
            db2 = lambda_secrets.resolve_db_connect_string()
    assert db1 == db2 == "postgresql://pooler/example"
    mock_sm.get_secret_value.assert_called_once()


def test_resolve_hevy_api_key_prefers_env() -> None:
    mock_sm = MagicMock()
    with patch.dict(
        "os.environ",
        {
            "HEVY_API_KEY": "hk-from-env",
            ENV_SOMA_HEVY_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/hevy",
        },
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_hevy_api_key()
    assert out == "hk-from-env"
    mock_sm.get_secret_value.assert_not_called()


def test_resolve_hevy_api_key_from_secret_plain() -> None:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "hk-sm"}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"HEVY_API_KEY": "", ENV_SOMA_HEVY_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/hevy"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_hevy_api_key()
    assert out == "hk-sm"


def test_resolve_hevy_api_key_missing_raises() -> None:
    clear_runtime_secret_json_cache()
    with patch.dict("os.environ", {"HEVY_API_KEY": "", ENV_SOMA_HEVY_SECRET_ARN: ""}, clear=False):
        with pytest.raises(OSError, match="Missing HEVY_API_KEY"):
            lambda_secrets.resolve_hevy_api_key()


def test_resolve_soma_user_id_from_secret_plain() -> None:
    uid = "11111111-1111-1111-1111-111111111111"
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": uid}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"SOMA_USER_ID": "", ENV_SOMA_TENANT_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/tenant"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            out = lambda_secrets.resolve_soma_user_id()
    assert out == uid


def test_resolve_soma_user_id_update_me_raises() -> None:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": "update_me"}
    clear_runtime_secret_json_cache()
    with patch.dict(
        "os.environ",
        {"SOMA_USER_ID": "", ENV_SOMA_TENANT_SECRET_ARN: "arn:aws:secretsmanager:us-west-2:1:secret:soma/tenant"},
        clear=False,
    ):
        with patch("boto3.client", return_value=mock_sm):
            with pytest.raises(OSError, match="SOMA_USER_ID"):
                lambda_secrets.resolve_soma_user_id()

"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# Lambda handlers and ``pipeline.lambda_secrets`` lazy-import boto3. When boto3 is not
# installed (base venv without ``.[dev]``), stub the module so patches still work.
try:
    import boto3 as _boto3_check  # noqa: F401
except ImportError:
    _boto3_stub = ModuleType("boto3")

    def _unpatched_boto3_client(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boto3.client called without a test patch")

    _boto3_stub.client = _unpatched_boto3_client  # type: ignore[attr-defined]
    sys.modules["boto3"] = _boto3_stub


@pytest.fixture(autouse=True)
def _clear_webhook_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local ``.env`` may set webhook secrets; handler tests expect auth disabled."""
    monkeypatch.delenv("APPLE_HEALTH_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SOMA_APPLE_WEBHOOK_SECRET_ARN", raising=False)


@pytest.fixture(autouse=True)
def _clear_lambda_runtime_secret_cache() -> None:
    """Secrets Manager JSON is cached per ARN; clear so boto mocks do not leak."""
    from pipeline import lambda_secrets

    lambda_secrets.clear_runtime_secret_json_cache()
    yield
    lambda_secrets.clear_runtime_secret_json_cache()

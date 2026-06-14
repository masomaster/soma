"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_lambda_runtime_secret_cache() -> None:
    """Secrets Manager JSON is cached per ARN; clear so boto mocks do not leak."""
    from pipeline import lambda_secrets

    lambda_secrets.clear_runtime_secret_json_cache()
    yield
    lambda_secrets.clear_runtime_secret_json_cache()

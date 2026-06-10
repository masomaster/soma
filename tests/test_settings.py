"""Tests for pipeline environment resolution."""

import os

import pytest

from pipeline.settings import Environment, get_environment


def test_get_environment_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    assert get_environment() is Environment.LOCAL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("local", Environment.LOCAL),
        ("LOCAL", Environment.LOCAL),
        (" staging ", Environment.STAGING),
        ("prod", Environment.PROD),
    ],
)
def test_get_environment_accepts_valid_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: Environment
) -> None:
    monkeypatch.setenv("ENV", raw)
    assert get_environment() is expected


def test_get_environment_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(ValueError, match="Invalid ENV"):
        get_environment()

"""Tests for dashboard Supabase auth helpers."""

from __future__ import annotations

import json
import urllib.error

import pytest

from dashboard.auth import (
    AuthError,
    _normalize_email,
    _normalize_password,
    sign_in_with_password,
)


def test_normalize_email_strips_and_lowercases() -> None:
    assert _normalize_email("  User@Example.COM  ") == "user@example.com"


def test_normalize_password_strips_surrounding_whitespace() -> None:
    assert _normalize_password(" secret\n") == "secret"


def test_sign_in_normalizes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_auth_request(
        supabase_url: str,
        anon_key: str,
        path: str,
        payload: dict[str, str],
    ) -> dict:
        captured.update(payload)
        return {
            "user": {"id": "user-1", "email": "user@example.com"},
            "access_token": "access",
            "refresh_token": "refresh",
        }

    monkeypatch.setattr("dashboard.auth._auth_request", fake_auth_request)
    sign_in_with_password(
        email="  User@Example.com  ",
        password=" secret\n",
        supabase_url="https://example.supabase.co",
        anon_key="anon",
    )
    assert captured["email"] == "user@example.com"
    assert captured["password"] == "secret"


def test_auth_request_raises_parsed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}
    ).encode()

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        urllib.error.HTTPError,
        "read",
        lambda self: body,
        raising=False,
    )

    from dashboard.auth import _auth_request

    with pytest.raises(AuthError, match="Invalid login credentials"):
        _auth_request("https://example.supabase.co", "anon", "/auth/v1/token", {})

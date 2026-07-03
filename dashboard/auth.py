"""Supabase Auth helpers for the Phase 9 dashboard (Path A: user JWT).

Uses the Supabase Auth REST API with the anon key — no extra client library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class AuthError(Exception):
    """Raised when sign-in or sign-up fails."""


def _auth_request(
    supabase_url: str,
    anon_key: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base = supabase_url.rstrip("/")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(detail)
            msg = parsed.get("msg") or parsed.get("error_description") or parsed.get("message")
            if msg:
                raise AuthError(str(msg)) from None
        except (json.JSONDecodeError, AuthError):
            pass
        raise AuthError(detail or f"Auth HTTP {exc.code}") from None
    return json.loads(raw)


def sign_in_with_password(
    *,
    email: str,
    password: str,
    supabase_url: str,
    anon_key: str,
) -> dict[str, str]:
    """Return ``access_token``, ``refresh_token``, and ``user_id``."""
    data = _auth_request(
        supabase_url,
        anon_key,
        "/auth/v1/token?grant_type=password",
        {"email": email.strip(), "password": password},
    )
    user = data.get("user") or {}
    user_id = user.get("id")
    token = data.get("access_token")
    refresh = data.get("refresh_token")
    if not user_id or not token:
        raise AuthError("Sign-in response missing user or token")
    return {
        "access_token": str(token),
        "refresh_token": str(refresh or ""),
        "user_id": str(user_id),
        "email": str(user.get("email") or email),
    }


def refresh_session(
    *,
    refresh_token: str,
    supabase_url: str,
    anon_key: str,
) -> dict[str, str]:
    """Exchange a stored refresh token for a fresh session (sticky sessions).

    Supabase rotates refresh tokens on each use, so the returned
    ``refresh_token`` must replace the one that was persisted.
    """
    data = _auth_request(
        supabase_url,
        anon_key,
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": refresh_token},
    )
    user = data.get("user") or {}
    user_id = user.get("id")
    token = data.get("access_token")
    new_refresh = data.get("refresh_token")
    if not user_id or not token:
        raise AuthError("Refresh response missing user or token")
    return {
        "access_token": str(token),
        "refresh_token": str(new_refresh or refresh_token),
        "user_id": str(user_id),
        "email": str(user.get("email") or ""),
    }


def sign_up_with_password(
    *,
    email: str,
    password: str,
    supabase_url: str,
    anon_key: str,
) -> dict[str, str]:
    """Create an account; may require email confirmation depending on project settings."""
    data = _auth_request(
        supabase_url,
        anon_key,
        "/auth/v1/signup",
        {"email": email.strip(), "password": password},
    )
    user = data.get("user") or data
    user_id = user.get("id")
    token = data.get("access_token")
    if not user_id:
        raise AuthError("Sign-up response missing user id")
    if not token:
        raise AuthError(
            "Account created — check your email to confirm, then sign in."
        )
    return {
        "access_token": str(token),
        "refresh_token": str(data.get("refresh_token") or ""),
        "user_id": str(user_id),
        "email": str(user.get("email") or email),
    }


def auth_configured() -> bool:
    """True when Supabase URL + anon key are set for login UI."""
    import os

    return bool(
        os.environ.get("SUPABASE_URL", "").strip()
        and os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )

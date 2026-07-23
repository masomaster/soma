#!/usr/bin/env python3.14
"""One-shot helper: exchange a Dropbox auth code for a long-lived refresh token.

Redirect URI must match the Dropbox app console **exactly** (including trailing
slash). Default: ``http://localhost:8765/`` — add that string under OAuth 2 →
Redirect URIs. There is no separate “production” redirect; Lambda never uses it.

Prints JSON for Secrets Manager ``soma-dropbox`` / ``.env`` (copy then clear
scrollback). Does not write secrets to disk.
"""

from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_DEFAULT_REDIRECT = "http://localhost:8765/"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_REPO_ROOT / ".env")


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    typed = input(f"{label}{suffix}: ").strip()
    return typed or default


def main() -> int:
    import os

    _load_dotenv()
    app_key = _prompt("DROPBOX_APP_KEY", os.environ.get("DROPBOX_APP_KEY", "").strip())
    env_secret = os.environ.get("DROPBOX_APP_SECRET", "").strip()
    if env_secret:
        app_secret = env_secret
        print("Using DROPBOX_APP_SECRET from environment (not echoed).")
    else:
        app_secret = getpass.getpass("DROPBOX_APP_SECRET: ").strip()
    folder = _prompt("DROPBOX_FOLDER", os.environ.get("DROPBOX_FOLDER", "").strip())
    redirect = _prompt(
        "Redirect URI (must match Dropbox console exactly)",
        os.environ.get("DROPBOX_REDIRECT_URI", "").strip() or _DEFAULT_REDIRECT,
    )

    if not app_key or not app_secret:
        print("Need DROPBOX_APP_KEY and DROPBOX_APP_SECRET.", file=sys.stderr)
        return 1

    print(
        "\nBefore opening the URL: in Dropbox App Console → OAuth 2 → Redirect URIs,\n"
        f"add this EXACT string (copy/paste):\n\n  {redirect}\n"
    )

    auth_url = (
        "https://www.dropbox.com/oauth2/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": app_key,
                "response_type": "code",
                "token_access_type": "offline",
                "redirect_uri": redirect,
            }
        )
    )
    print("Open this URL, approve, then paste the ?code= value from the redirect:\n")
    print(auth_url)
    print()
    code = input("Authorization code: ").strip()
    if not code:
        print("No code; aborting.", file=sys.stderr)
        return 1
    if "code=" in code:
        parsed = urllib.parse.urlparse(code)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("code"):
            code = qs["code"][0]

    body = urllib.parse.urlencode(
        {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": app_key,
            "client_secret": app_secret,
            "redirect_uri": redirect,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.dropboxapi.com/oauth2/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read()[:800] if exc.fp else b""
        print(f"Token exchange failed HTTP {exc.code}: {err!r}", file=sys.stderr)
        print(
            "Usually means redirect_uri here ≠ console, or code already used.",
            file=sys.stderr,
        )
        return 1

    refresh = str(data.get("refresh_token") or "").strip()
    if not refresh:
        print("Response missing refresh_token — ensure token_access_type=offline.", file=sys.stderr)
        print(json.dumps({k: v for k, v in data.items() if k != "access_token"}, indent=2), file=sys.stderr)
        return 1

    secret = {
        "DROPBOX_APP_KEY": app_key,
        "DROPBOX_APP_SECRET": app_secret,
        "DROPBOX_REFRESH_TOKEN": refresh,
        "DROPBOX_FOLDER": folder,
    }
    print(
        "\nCopy into Secrets Manager soma-dropbox and DROPBOX_* in .env, "
        "then clear this terminal scrollback:\n"
    )
    print(json.dumps(secret, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

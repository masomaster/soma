"""Minimal Dropbox HTTP client (list + download) via urllib — no SDK.

Uses OAuth2 **refresh tokens** (``token_access_type=offline``). Short-lived access
tokens are refreshed in-process; secrets never go to logs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DROPBOX_LIST_URL = "https://api.dropboxapi.com/2/files/list_folder"
DROPBOX_LIST_CONTINUE_URL = "https://api.dropboxapi.com/2/files/list_folder/continue"
DROPBOX_DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"


class DropboxApiError(Exception):
    """Dropbox HTTP / API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DropboxFileEntry:
    """A downloadable file in a Dropbox folder."""

    name: str
    path_display: str
    path_lower: str
    content_hash: str | None
    client_modified: str | None = None


def refresh_access_token(
    *,
    app_key: str,
    app_secret: str,
    refresh_token: str,
    urlopen: Any = urllib.request.urlopen,
) -> str:
    """Exchange a refresh token for a short-lived access token."""
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
            "client_secret": app_secret,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        DROPBOX_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        err_body = exc.read()[:500] if exc.fp else b""
        raise DropboxApiError(
            f"Dropbox token refresh failed HTTP {exc.code}: {err_body!r}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise DropboxApiError(f"Dropbox token refresh transport error: {exc}") from exc
    if status and int(status) >= 400:
        raise DropboxApiError(f"Dropbox token refresh HTTP {status}", status_code=int(status))
    data = json.loads(raw.decode("utf-8"))
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise DropboxApiError("Dropbox token refresh response missing access_token")
    return token


def _post_json(
    url: str,
    *,
    access_token: str,
    payload: dict[str, Any],
    urlopen: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=120) as resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        err_body = exc.read()[:800] if exc.fp else b""
        raise DropboxApiError(
            f"Dropbox API {url} HTTP {exc.code}: {err_body!r}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise DropboxApiError(f"Dropbox API transport error: {exc}") from exc
    if status and int(status) >= 400:
        raise DropboxApiError(f"Dropbox API HTTP {status}", status_code=int(status))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise DropboxApiError("Dropbox API returned non-object JSON")
    return data


def iter_folder_files(
    *,
    access_token: str,
    folder_path: str = "",
    recursive: bool = False,
    urlopen: Any = urllib.request.urlopen,
) -> Iterator[DropboxFileEntry]:
    """Yield file entries under ``folder_path`` (``\"\"`` = account/app root).

    Default ``recursive=False`` — Wahoo writes FITs into a flat folder
    (``/Apps/WahooFitness``); avoid walking the whole Dropbox tree.
    """
    path = folder_path.strip()
    # Dropbox root for app folder or full Dropbox is empty string, not "/".
    if path == "/":
        path = ""
    result = _post_json(
        DROPBOX_LIST_URL,
        access_token=access_token,
        payload={"path": path, "recursive": recursive},
        urlopen=urlopen,
    )
    while True:
        for entry in result.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get(".tag") != "file":
                continue
            name = str(entry.get("name") or "")
            path_display = str(entry.get("path_display") or entry.get("path_lower") or "")
            path_lower = str(entry.get("path_lower") or path_display.lower())
            if not name or not path_lower:
                continue
            ch = entry.get("content_hash")
            modified = entry.get("client_modified") or entry.get("server_modified")
            yield DropboxFileEntry(
                name=name,
                path_display=path_display,
                path_lower=path_lower,
                content_hash=str(ch) if isinstance(ch, str) else None,
                client_modified=str(modified) if isinstance(modified, str) else None,
            )
        if not result.get("has_more"):
            break
        cursor = result.get("cursor")
        if not isinstance(cursor, str) or not cursor:
            break
        result = _post_json(
            DROPBOX_LIST_CONTINUE_URL,
            access_token=access_token,
            payload={"cursor": cursor},
            urlopen=urlopen,
        )


def download_file(
    *,
    access_token: str,
    path: str,
    urlopen: Any = urllib.request.urlopen,
) -> bytes:
    """Download file bytes at ``path`` (``path_lower`` or ``path_display``)."""
    arg = json.dumps({"path": path})
    req = urllib.request.Request(
        DROPBOX_DOWNLOAD_URL,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Dropbox-API-Arg": arg,
        },
    )
    try:
        with urlopen(req, timeout=180) as resp:
            data = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        err_body = exc.read()[:500] if exc.fp else b""
        raise DropboxApiError(
            f"Dropbox download failed HTTP {exc.code}: {err_body!r}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise DropboxApiError(f"Dropbox download transport error: {exc}") from exc
    if status and int(status) >= 400:
        raise DropboxApiError(f"Dropbox download HTTP {status}", status_code=int(status))
    logger.debug("Downloaded Dropbox path=%s bytes=%d", path, len(data))
    return data


def is_activity_filename(name: str) -> bool:
    """True for FIT/TCX/GPX activity files (including ``.gz``)."""
    lower = name.lower()
    return lower.endswith(
        (".fit", ".fit.gz", ".tcx", ".tcx.gz", ".gpx", ".gpx.gz")
    )

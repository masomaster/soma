"""Hermetic tests for :mod:`pipeline.dropbox_api`."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from pipeline.dropbox_api import (
    DropboxApiError,
    DropboxFileEntry,
    download_file,
    is_activity_filename,
    iter_activity_files_newest_first,
    iter_folder_files,
    refresh_access_token,
)


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_is_activity_filename() -> None:
    assert is_activity_filename("ride.fit")
    assert is_activity_filename("ride.FIT.GZ")
    assert is_activity_filename("a.tcx.gz")
    assert not is_activity_filename("notes.txt")


def test_refresh_access_token_ok() -> None:
    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        assert "oauth2/token" in req.full_url
        return _FakeResp(json.dumps({"access_token": "at-1"}).encode())

    token = refresh_access_token(
        app_key="k", app_secret="s", refresh_token="rt", urlopen=urlopen
    )
    assert token == "at-1"


def test_refresh_access_token_http_error() -> None:
    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        raise HTTPError(req.full_url, 400, "bad", hdrs=None, fp=io.BytesIO(b"nope"))

    with pytest.raises(DropboxApiError, match="token refresh"):
        refresh_access_token(
            app_key="k", app_secret="s", refresh_token="rt", urlopen=urlopen
        )


def test_iter_folder_files_filters_and_paginates() -> None:
    calls: list[str] = []

    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        calls.append(req.full_url)
        body = json.loads(req.data.decode())
        if "continue" in req.full_url:
            assert "cursor" in body
            payload = {
                "entries": [
                    {
                        ".tag": "file",
                        "name": "b.fit",
                        "path_display": "/Apps/WahooFitness/b.fit",
                        "path_lower": "/apps/wahoofitness/b.fit",
                        "content_hash": "h2",
                        "client_modified": "2026-07-20T12:00:00Z",
                    }
                ],
                "has_more": False,
            }
        else:
            assert body.get("recursive") is False
            payload = {
                "entries": [
                    {".tag": "folder", "name": "sub", "path_lower": "/apps/wahoofitness/sub"},
                    {
                        ".tag": "file",
                        "name": "a.fit",
                        "path_display": "/Apps/WahooFitness/a.fit",
                        "path_lower": "/apps/wahoofitness/a.fit",
                        "content_hash": "h1",
                        "client_modified": "2026-07-21T12:00:00Z",
                    },
                    {
                        ".tag": "file",
                        "name": "readme.txt",
                        "path_lower": "/apps/wahoofitness/readme.txt",
                    },
                ],
                "has_more": True,
                "cursor": "c1",
            }
        return _FakeResp(json.dumps(payload).encode())

    files = list(
        iter_folder_files(access_token="at", folder_path="/Apps/WahooFitness", urlopen=urlopen)
    )
    assert len(files) == 3
    assert files[0] == DropboxFileEntry(
        name="a.fit",
        path_display="/Apps/WahooFitness/a.fit",
        path_lower="/apps/wahoofitness/a.fit",
        content_hash="h1",
        client_modified="2026-07-21T12:00:00Z",
    )
    assert files[1].name == "readme.txt"
    assert files[2].name == "b.fit"
    assert any("continue" in u for u in calls)


def test_iter_folder_files_has_more_without_cursor_raises() -> None:
    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        payload = {
            "entries": [
                {
                    ".tag": "file",
                    "name": "a.fit",
                    "path_display": "/Apps/WahooFitness/a.fit",
                    "path_lower": "/apps/wahoofitness/a.fit",
                    "content_hash": "h1",
                }
            ],
            "has_more": True,
            "cursor": "",
        }
        return _FakeResp(json.dumps(payload).encode())

    with pytest.raises(DropboxApiError, match="cursor is missing"):
        list(
            iter_folder_files(
                access_token="at", folder_path="/Apps/WahooFitness", urlopen=urlopen
            )
        )


def test_iter_activity_files_newest_first_search_and_continue() -> None:
    calls: list[str] = []

    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        calls.append(req.full_url)
        if "continue" in req.full_url:
            payload = {
                "matches": [
                    {
                        "metadata": {
                            ".tag": "metadata",
                            "metadata": {
                                ".tag": "file",
                                "name": "older.fit",
                                "path_display": "/Apps/WahooFitness/older.fit",
                                "path_lower": "/apps/wahoofitness/older.fit",
                                "content_hash": "h2",
                                "client_modified": "2026-07-01T12:00:00Z",
                            },
                        }
                    }
                ],
                "has_more": False,
            }
        else:
            body = json.loads(req.data.decode())
            assert body["query"] == ""
            opts = body["options"]
            assert opts["order_by"] == "last_modified_time"
            assert "fit" in opts["file_extensions"]
            payload = {
                "matches": [
                    {
                        "metadata": {
                            ".tag": "metadata",
                            "metadata": {
                                ".tag": "file",
                                "name": "newer.fit",
                                "path_display": "/Apps/WahooFitness/newer.fit",
                                "path_lower": "/apps/wahoofitness/newer.fit",
                                "content_hash": "h1",
                                "client_modified": "2026-07-20T12:00:00Z",
                            },
                        }
                    },
                    {
                        "metadata": {
                            ".tag": "metadata",
                            "metadata": {
                                ".tag": "file",
                                "name": "notes.gz",
                                "path_display": "/Apps/WahooFitness/notes.gz",
                                "path_lower": "/apps/wahoofitness/notes.gz",
                                "content_hash": "skip",
                            },
                        }
                    },
                ],
                "has_more": True,
                "cursor": "sc1",
            }
        return _FakeResp(json.dumps(payload).encode())

    files = list(
        iter_activity_files_newest_first(
            access_token="at", folder_path="/Apps/WahooFitness", urlopen=urlopen
        )
    )
    assert [f.name for f in files] == ["newer.fit", "older.fit"]
    assert any("search/continue_v2" in u for u in calls)


def test_iter_activity_files_has_more_without_cursor_raises() -> None:
    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        payload = {
            "matches": [],
            "has_more": True,
        }
        return _FakeResp(json.dumps(payload).encode())

    with pytest.raises(DropboxApiError, match="cursor is missing"):
        list(
            iter_activity_files_newest_first(
                access_token="at", folder_path="/Apps/WahooFitness", urlopen=urlopen
            )
        )


def test_download_file_ok() -> None:
    def urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        headers = {k.lower(): v for k, v in req.header_items()}
        assert "dropbox-api-arg" in headers
        assert "authorization" in headers
        return _FakeResp(b"\x0e\x10fit")

    data = download_file(access_token="at", path="/apps/wahoofitness/a.fit", urlopen=urlopen)
    assert data.startswith(b"\x0e")

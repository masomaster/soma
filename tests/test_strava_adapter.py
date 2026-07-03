"""Strava adapter — raw path, normalize activities array, cardio upsert wiring."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.adapters import strava
from pipeline.cardio_upsert import upsert_cardio_events
from pipeline.raw_storage import format_raw_object_key

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_USER = "00000000-0000-0000-0000-000000000001"


def _load_strava_page() -> list:
    path = _FIXTURES / "strava" / "activities_page_redacted.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_normalize_strava_activities_maps_cardio_row_shape() -> None:
    payload = _load_strava_page()
    rows = strava.normalize_strava_activities(payload, _USER)
    assert len(rows) == 2
    assert rows[0]["source_id"] == "strava:12345678901"
    assert rows[0]["user_id"] == _USER
    assert rows[0]["source"] == "strava"
    assert rows[0]["event_date"] == date(2024, 6, 1)
    assert rows[0]["activity_type"] == "Run"
    assert rows[0]["duration_min"] == pytest.approx(40.0)
    assert rows[0]["distance_miles"] == pytest.approx(8046.8 / strava.METERS_PER_MILE, rel=1e-5)
    assert rows[0]["elevation_ft"] == pytest.approx(42.5 * strava.METERS_TO_FEET, rel=1e-3)
    assert rows[0]["avg_hr"] is None
    assert rows[0]["max_hr"] is None
    assert rows[0]["calories"] is None
    assert rows[0]["notes"] == "Morning run (redacted)"
    assert rows[0]["session_rpe"] is None
    assert rows[1]["avg_hr"] == 142
    assert rows[1]["max_hr"] == 168
    assert rows[1]["calories"] == int(round(800 * strava.KJ_TO_KCAL))


def test_fetch_and_normalize_writes_raw_then_returns_rows() -> None:
    payload = _load_strava_page()
    captured: list[tuple[str, bytes]] = []
    utc = datetime(2024, 6, 3, 12, 0, 0, tzinfo=timezone.utc)

    def raw_put(key: str, body: bytes) -> None:
        captured.append((key, body))

    rows = strava.fetch_and_normalize(
        _USER,
        fetch_all_pages=lambda: [payload],
        raw_put=raw_put,
        utc_now=utc,
    )
    assert len(rows) == 2
    assert len(captured) == 1
    key, body = captured[0]
    assert key.startswith(f"raw/{_USER}/strava/2024-06-03/")
    assert key.endswith(".json")
    written = json.loads(body.decode("utf-8"))
    assert len(written) == 2


def test_format_raw_object_key_strava_source() -> None:
    at = datetime(2024, 6, 1, 18, 30, 0, 123456, tzinfo=timezone.utc)
    key = format_raw_object_key(_USER, strava.CARDIO_SOURCE, at)
    assert key == "raw/00000000-0000-0000-0000-000000000001/strava/2024-06-01/183000_123456.json"


def test_upsert_cardio_events_uses_on_conflict_do_nothing() -> None:
    cur = MagicMock()
    row = {
        "user_id": _USER,
        "source": "strava",
        "source_app": None,
        "source_id": "strava:999",
        "event_date": date(2024, 6, 1),
        "started_at": None,
        "activity_type": "Run",
        "duration_min": 30.0,
        "distance_miles": 3.0,
        "elevation_ft": None,
        "avg_hr": None,
        "max_hr": None,
        "avg_pace_sec_mi": None,
        "calories": None,
        "effort_zone": None,
        "session_rpe": None,
        "notes": None,
    }
    with patch("pipeline.cardio_upsert.execute_values") as ev:
        upsert_cardio_events(cur, [row])
    ev.assert_called_once()
    _c, sql, values = ev.call_args[0]
    assert "INSERT INTO cardio_events" in sql
    assert "ON CONFLICT (user_id, source_id) DO NOTHING" in sql
    assert len(values) == 1
    assert values[0][0] == _USER
    assert values[0][3] == "strava:999"


def test_fetch_strava_activities_page_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(req: urllib.request.Request, timeout: int = 60) -> _Resp:
        calls.append(req.full_url)
        hdrs = {k.lower(): v for k, v in req.header_items()}
        assert hdrs.get("authorization") == "Bearer secret-token"
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = strava.fetch_strava_activities_page("secret-token", page=2, per_page=30)
    assert out == []
    assert len(calls) == 1
    assert "page=2" in calls[0]
    assert "per_page=30" in calls[0]
    assert calls[0].startswith(strava.STRAVA_API_BASE)


def test_fetch_strava_activities_page_passes_before_after(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(req: urllib.request.Request, timeout: int = 60) -> _Resp:
        calls.append(req.full_url)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    strava.fetch_strava_activities_page("t", page=1, before=1_700_000_000, after=1_600_000_000)
    assert "before=1700000000" in calls[0]
    assert "after=1600000000" in calls[0]


def test_fetch_strava_activities_page_http_429_wraps_with_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError(
            "https://www.strava.com/api/v3/athlete/activities",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(strava.StravaRequestError, match="HTTP 429") as ei:
        strava.fetch_strava_activities_page("t")
    assert ei.value.status_code == 429
    assert ei.value.retry_suggested is True


def test_fetch_strava_activities_page_dict_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"message":"Authorization Error"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    with pytest.raises(ValueError, match="array"):
        strava.fetch_strava_activities_page("t")


def test_fetch_strava_activities_page_rejects_non_strava_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        strava.fetch_strava_activities_page("t", base_url="https://evil.example/api/v3")


def test_build_fetch_all_activity_pages_stops_on_short_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_row = [
        {
            "id": 1,
            "type": "Run",
            "moving_time": 60,
            "start_date_local": "2024-01-01T00:00:00",
        }
    ]

    def fake_fetch(
        access_token: str,
        *,
        page: int = 1,
        per_page: int = strava.DEFAULT_PER_PAGE,
        before: int | None = None,
        after: int | None = None,
        base_url: str = strava.STRAVA_API_BASE,
    ) -> list:
        if page == 1:
            return one_row
        return []

    monkeypatch.setattr(strava, "fetch_strava_activities_page", fake_fetch)
    fn = strava.build_fetch_all_activity_pages("tok", per_page=1, max_pages=10)
    pages = fn()
    assert len(pages) == 2
    assert pages[0] == one_row
    assert pages[1] == []


def test_build_fetch_all_activity_pages_raises_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def fake_fetch(
        access_token: str,
        *,
        page: int = 1,
        per_page: int = strava.DEFAULT_PER_PAGE,
        before: int | None = None,
        after: int | None = None,
        base_url: str = strava.STRAVA_API_BASE,
    ) -> list:
        return [{"id": page, "type": "Run", "moving_time": 60, "start_date_local": "2024-01-01T00:00:00"}]

    monkeypatch.setattr(strava, "fetch_strava_activities_page", fake_fetch)
    fn = strava.build_fetch_all_activity_pages("tok", per_page=1, max_pages=2)
    with pytest.raises(RuntimeError, match="max_pages"):
        fn()


def test_build_fetch_all_activity_pages_rejects_max_pages_below_one() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        strava.build_fetch_all_activity_pages("tok", max_pages=0)

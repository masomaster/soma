"""Phase 3: Hevy adapter — raw path, normalize list payload, orchestrated ingest."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.adapters import hevy
from pipeline.raw_storage import format_raw_object_key
from pipeline.strength_upsert import upsert_strength_events

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_USER = "00000000-0000-0000-0000-000000000001"


def _load_hevy_page1() -> dict:
    path = _FIXTURES / "hevy" / "get_workouts_page1_redacted.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_format_raw_object_key_matches_workspace_rule() -> None:
    at = datetime(2024, 6, 1, 18, 30, 0, 123456, tzinfo=timezone.utc)
    key = format_raw_object_key(_USER, "hevy", at)
    assert key == "raw/00000000-0000-0000-0000-000000000001/hevy/2024-06-01/183000_123456.json"


def test_normalize_hevy_list_workouts_emits_source_ids_and_lbs() -> None:
    payload = _load_hevy_page1()
    rows = hevy.normalize_hevy_list_workouts(payload, _USER)
    assert len(rows) == 2
    w_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert rows[0]["source_id"] == f"hevy:{w_id}:0:0"
    assert rows[1]["source_id"] == f"hevy:{w_id}:0:1"
    assert rows[0]["user_id"] == _USER
    assert rows[0]["source"] == "hevy"
    assert rows[0]["event_date"].isoformat() == "2024-06-01"
    assert rows[0]["exercise_name"] == "Bench Press (Barbell)"
    assert rows[0]["set_type"] == "warmup"
    assert rows[0]["reps"] == 10
    assert rows[0]["weight_lbs"] == pytest.approx(60.0 * hevy.KG_TO_LBS, rel=1e-5)
    assert rows[1]["set_type"] == "working"
    assert rows[1]["rpe"] == 8.0
    assert rows[1]["weight_lbs"] == pytest.approx(90.0 * hevy.KG_TO_LBS, rel=1e-5)
    assert rows[0]["superset_id"] is None


def test_fetch_and_normalize_writes_raw_then_returns_rows() -> None:
    payload = _load_hevy_page1()
    captured: list[tuple[str, bytes]] = []
    utc = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def raw_put(key: str, body: bytes) -> None:
        captured.append((key, body))

    rows = hevy.fetch_and_normalize(
        _USER,
        fetch_all_pages=lambda: [payload],
        raw_put=raw_put,
        utc_now=utc,
    )
    assert len(rows) == 2
    assert len(captured) == 1
    key, body = captured[0]
    assert key.startswith(f"raw/{_USER}/hevy/2024-06-01/")
    assert key.endswith(".json")
    written = json.loads(body.decode("utf-8"))
    assert written["page"] == 1
    assert len(written["workouts"]) == 1


def test_upsert_strength_events_uses_on_conflict_do_nothing() -> None:
    cur = MagicMock()
    row = {
        "user_id": _USER,
        "source": "hevy",
        "source_id": "hevy:test-w:0:0",
        "event_date": "2024-06-01",
        "exercise_name": "Squat",
        "muscle_group": None,
        "movement_type": None,
        "superset_id": None,
        "set_number": 1,
        "reps": 5,
        "weight_lbs": 225.0,
        "rpe": None,
        "set_type": "working",
        "notes": None,
    }
    with patch("pipeline.strength_upsert.execute_values") as ev:
        upsert_strength_events(cur, [row])
    ev.assert_called_once()
    _c, sql, values = ev.call_args[0]
    assert "INSERT INTO strength_events" in sql
    assert "ON CONFLICT (user_id, source_id) DO NOTHING" in sql
    assert len(values) == 1
    assert values[0][0] == _USER
    assert values[0][2] == "hevy:test-w:0:0"


def test_fetch_hevy_workouts_page_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"page":1,"page_count":1,"workouts":[]}'

    def fake_urlopen(req: urllib.request.Request, timeout: int = 60) -> _Resp:
        calls.append(req.full_url)
        hdrs = {k.lower(): v for k, v in req.header_items()}
        assert hdrs.get("api-key") == "secret"
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = hevy.fetch_hevy_workouts_page("secret", page=2, page_size=10)
    assert out == {"page": 1, "page_count": 1, "workouts": []}
    assert len(calls) == 1
    assert "page=2" in calls[0]
    assert "pageSize=10" in calls[0]


def test_fetch_hevy_workouts_page_http_429_wraps_with_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError(
            "https://api.hevyapp.com/v1/workouts",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"detail":"slow down"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(hevy.HevyRequestError, match="HTTP 429") as ei:
        hevy.fetch_hevy_workouts_page("secret")
    assert ei.value.status_code == 429
    assert ei.value.retry_suggested is True


def test_fetch_hevy_workouts_page_http_401_not_retry_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError(
            "https://api.hevyapp.com/v1/workouts",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(hevy.HevyRequestError, match="HTTP 401") as ei:
        hevy.fetch_hevy_workouts_page("secret")
    assert ei.value.status_code == 401
    assert ei.value.retry_suggested is False


def test_fetch_hevy_workouts_page_url_error_wraps_with_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(hevy.HevyRequestError, match="connection reset") as ei:
        hevy.fetch_hevy_workouts_page("secret")
    assert ei.value.status_code is None
    assert ei.value.retry_suggested is True


def test_fetch_hevy_workouts_page_rejects_non_hevy_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        hevy.fetch_hevy_workouts_page("secret", base_url="https://evil.example/v1")


def test_fetch_hevy_workouts_page_accepts_trailing_slash_on_official_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"page":1,"page_count":1,"workouts":[]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    out = hevy.fetch_hevy_workouts_page(
        "secret",
        base_url="https://api.hevyapp.com/v1/",
    )
    assert out["page_count"] == 1


def test_fetch_hevy_workouts_page_invalid_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not json{"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    with pytest.raises(ValueError, match="valid JSON"):
        hevy.fetch_hevy_workouts_page("secret")


def test_fetch_hevy_workouts_page_invalid_page_count_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"page":1,"page_count":"nope","workouts":[]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())

    def _fetch() -> None:
        fn = hevy.build_fetch_all_workout_pages("k", max_pages=5)
        fn()

    with pytest.raises(ValueError, match="page_count"):
        _fetch()


def test_build_fetch_all_workout_pages_raises_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch(
        api_key: str,
        *,
        page: int = 1,
        page_size: int = 10,
        base_url: str = hevy.HEVY_API_BASE,
    ) -> dict:
        return {"page": page, "page_count": 9999, "workouts": []}

    monkeypatch.setattr(hevy, "fetch_hevy_workouts_page", fake_fetch)
    fn = hevy.build_fetch_all_workout_pages("k", max_pages=2)
    with pytest.raises(RuntimeError, match="max_pages"):
        fn()


def test_build_fetch_all_workout_pages_rejects_max_pages_below_one() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        hevy.build_fetch_all_workout_pages("k", max_pages=0)

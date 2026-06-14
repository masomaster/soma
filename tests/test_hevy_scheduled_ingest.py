"""Scheduled Hevy ingest orchestration (no HTTP, no real DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pipeline.hevy_scheduled_ingest import run_hevy_scheduled_ingest

_USER = "00000000-0000-0000-0000-000000000001"


def test_run_hevy_scheduled_ingest_writes_raw_and_upserts() -> None:
    raw_keys: list[str] = []
    utc = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def raw_put(key: str, body: bytes) -> None:
        raw_keys.append(key)

    row = {
        "user_id": _USER,
        "source": "hevy",
        "source_id": "hevy:w:0:0",
        "event_date": utc.date(),
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

    def fake_fetch(
        uid: str,
        api_key: str,
        *,
        raw_put,
        utc_now,
        max_pages: int = 500,
    ) -> list[dict]:
        assert uid == _USER
        assert api_key == "k"
        raw_put("raw/x/y.json", b"{}")
        return [row]

    conn = MagicMock()
    cur = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = None
    conn.cursor.return_value = cursor_cm
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None

    with (
        patch("pipeline.hevy_scheduled_ingest.psycopg2.connect", return_value=conn) as pc,
        patch("pipeline.hevy_scheduled_ingest.upsert_strength_events") as us,
    ):
        out = run_hevy_scheduled_ingest(
            user_id=_USER,
            api_key="k",
            dsn="postgresql://test",
            raw_put=raw_put,
            utc_now=utc,
            fetch_normalize=fake_fetch,
        )

    assert out == {"ok": True, "strength_rows": 1}
    assert raw_keys == ["raw/x/y.json"]
    pc.assert_called_once_with("postgresql://test")
    us.assert_called_once_with(cur, [row])
    conn.close.assert_called_once()

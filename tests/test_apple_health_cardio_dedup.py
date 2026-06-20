"""Near-duplicate Apple Health hub ``cardio_events`` deduplication."""

from __future__ import annotations

from datetime import date

from pipeline.apple_health_cardio_dedup import (
    filter_near_duplicate_apple_cardio,
    near_duplicate_apple_cardio,
)


def _row(
    *,
    source_id: str,
    event_date: date,
    activity_type: str = "Outdoor Run",
    duration_min: float = 30.0,
    distance_miles: float | None = 3.0,
    avg_hr: int | None = None,
) -> dict:
    return {
        "user_id": "u",
        "source": "apple_health",
        "source_id": source_id,
        "event_date": event_date,
        "activity_type": activity_type,
        "duration_min": duration_min,
        "distance_miles": distance_miles,
        "elevation_ft": None,
        "avg_hr": avg_hr,
        "max_hr": None,
        "avg_pace_sec_mi": None,
        "calories": None,
        "effort_zone": None,
        "session_rpe": None,
        "notes": None,
    }


def test_near_duplicate_same_session_different_uuid() -> None:
    d = date(2024, 6, 1)
    a = _row(source_id="apple_health:uuid-a", event_date=d)
    b = _row(source_id="apple_health:uuid-b", event_date=d, duration_min=32.0)
    assert near_duplicate_apple_cardio(a, b)


def test_not_duplicate_different_activity() -> None:
    d = date(2024, 6, 1)
    a = _row(source_id="apple_health:1", event_date=d, activity_type="Outdoor Run")
    b = _row(source_id="apple_health:2", event_date=d, activity_type="Cycling")
    assert not near_duplicate_apple_cardio(a, b)


class _FakeCursor:
    def __init__(self, existing: list[dict]) -> None:
        self._existing = existing

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._params = params

    def fetchall(self) -> list[tuple]:
        rows = []
        for r in self._existing:
            rows.append(
                (
                    r["source"],
                    r["source_id"],
                    r["event_date"],
                    r["activity_type"],
                    r["duration_min"],
                    r["distance_miles"],
                    r["elevation_ft"],
                    r["avg_hr"],
                    r["max_hr"],
                    r["avg_pace_sec_mi"],
                    r["calories"],
                    r["effort_zone"],
                    r["session_rpe"],
                    r["notes"],
                )
            )
        return rows


def test_filter_dedupes_batch_and_keeps_richer_row() -> None:
    d = date(2024, 6, 2)
    sparse = _row(source_id="apple_health:sparse", event_date=d, distance_miles=None)
    rich = _row(
        source_id="apple_health:rich",
        event_date=d,
        distance_miles=3.1,
        avg_hr=150,
    )
    cur = _FakeCursor([])
    kept, dropped = filter_near_duplicate_apple_cardio(
        cur, user_id="00000000-0000-0000-0000-000000000001", cardio_rows=[sparse, rich]
    )
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0]["source_id"] == "apple_health:rich"


def test_filter_drops_incoming_when_db_has_near_match() -> None:
    d = date(2024, 6, 3)
    existing = _row(source_id="apple_health:already", event_date=d)
    incoming = _row(source_id="apple_health:health-sync-dup", event_date=d, duration_min=31.0)
    cur = _FakeCursor([existing])
    kept, dropped = filter_near_duplicate_apple_cardio(
        cur, user_id="00000000-0000-0000-0000-000000000001", cardio_rows=[incoming]
    )
    assert dropped == 1
    assert kept == []

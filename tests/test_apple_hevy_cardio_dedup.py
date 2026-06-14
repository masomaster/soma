"""Hevy vs Apple Health strength ``cardio_events`` deduplication."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.apple_hevy_cardio_dedup import (
    filter_apple_strength_cardio_when_hevy_present,
    is_apple_strength_cardio_hevy_dup_candidate,
)


def _apple_health_cardio_row(
    *,
    source_id: str,
    event_date: date | str,
    activity_type: str,
    user_id: str = "u",
    duration_min: float = 45.0,
    distance_miles: float | None = None,
    avg_hr: int | None = None,
    max_hr: int | None = None,
    avg_pace_sec_mi: int | None = None,
    calories: int | None = None,
) -> dict:
    return {
        "user_id": user_id,
        "source": "apple_health",
        "source_id": source_id,
        "event_date": event_date,
        "activity_type": activity_type,
        "duration_min": duration_min,
        "distance_miles": distance_miles,
        "elevation_ft": None,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_pace_sec_mi": avg_pace_sec_mi,
        "calories": calories,
        "effort_zone": None,
        "session_rpe": None,
        "notes": None,
    }


def test_candidate_only_apple_strength_types() -> None:
    assert is_apple_strength_cardio_hevy_dup_candidate(
        {
            "source": "apple_health",
            "activity_type": "Traditional Strength Training",
            "event_date": date(2024, 6, 1),
        }
    )
    assert not is_apple_strength_cardio_hevy_dup_candidate(
        {
            "source": "apple_health",
            "activity_type": "Outdoor Run",
            "event_date": date(2024, 6, 1),
        }
    )
    assert not is_apple_strength_cardio_hevy_dup_candidate(
        {
            "source": "strava",
            "activity_type": "Traditional Strength Training",
            "event_date": date(2024, 6, 1),
        }
    )


class _FakeCursor:
    """Returns 2024-06-01 as a Hevy day for DISTINCT query."""

    def __init__(self, hevy_dates: list[date]) -> None:
        self._hevy_dates = hevy_dates
        self.params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.params = params

    def fetchall(self) -> list[tuple[date]]:
        return [(d,) for d in self._hevy_dates]


def test_filter_drops_apple_strength_when_hevy_same_day() -> None:
    d = date(2024, 6, 1)
    cardio = [
        _apple_health_cardio_row(
            source_id="apple_health:hk-1",
            event_date=d,
            activity_type="Traditional Strength Training",
        ),
        _apple_health_cardio_row(
            source_id="apple_health:hk-2",
            event_date=d,
            activity_type="Outdoor Run",
            duration_min=30.0,
            distance_miles=3.0,
            avg_hr=140,
            max_hr=160,
            avg_pace_sec_mi=600,
            calories=300,
        ),
    ]
    cur = _FakeCursor([d])
    kept, dropped = filter_apple_strength_cardio_when_hevy_present(cur, user_id="00000000-0000-0000-0000-000000000001", cardio_rows=cardio)
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0]["activity_type"] == "Outdoor Run"


def test_filter_drops_strength_when_event_date_is_iso_string() -> None:
    """``event_date`` may arrive as an ISO string; dedup must still match Hevy days."""
    d = date(2024, 6, 1)
    cardio = [
        _apple_health_cardio_row(
            source_id="apple_health:hk-1",
            event_date="2024-06-01",
            activity_type="Traditional Strength Training",
        ),
    ]
    cur = _FakeCursor([d])
    kept, dropped = filter_apple_strength_cardio_when_hevy_present(cur, user_id="00000000-0000-0000-0000-000000000001", cardio_rows=cardio)
    assert dropped == 1
    assert kept == []


def test_filter_keeps_strength_when_no_hevy_that_day() -> None:
    d = date(2024, 6, 2)
    cardio = [
        _apple_health_cardio_row(
            source_id="apple_health:hk-1",
            event_date=d,
            activity_type="Traditional Strength Training",
        ),
    ]
    cur = _FakeCursor([])
    kept, dropped = filter_apple_strength_cardio_when_hevy_present(cur, user_id="00000000-0000-0000-0000-000000000001", cardio_rows=cardio)
    assert dropped == 0
    assert len(kept) == 1

"""Tests for workout calendar aggregation and streaks."""

from __future__ import annotations

from datetime import date

from pipeline.workout_calendar import (
    build_month_grid,
    build_workout_calendar,
    build_workout_day_map,
    compute_streaks,
    is_fitbit_origin_activity,
    month_bounds,
    previous_month,
)


def test_is_fitbit_origin_activity_detects_bridges():
    assert is_fitbit_origin_activity("Health Sync")
    assert is_fitbit_origin_activity("Fitbit")
    assert is_fitbit_origin_activity("Google Fit")
    assert not is_fitbit_origin_activity("Nike Run Club")
    assert not is_fitbit_origin_activity(None)


def test_build_workout_day_map_merges_lifting_cardio_fitbit():
    strength = [
        {"event_date": "2026-07-10", "exercise_name": "Bench"},
        {"event_date": "2026-07-10", "exercise_name": "Squat"},
    ]
    cardio = [
        {
            "event_date": "2026-07-10",
            "activity_type": "Outdoor Run",
            "source": "apple_health",
            "source_app": "Nike Run Club",
        },
        {
            "event_date": "2026-07-11",
            "activity_type": "Walking",
            "source": "apple_health",
            "source_app": "Health Sync",
        },
        {
            "event_date": "2026-07-12",
            "activity_type": "Traditional Strength Training",
            "source": "apple_health",
            "source_app": "Mason's Apple Watch",
        },
    ]
    days = build_workout_day_map(strength, cardio)
    assert days[date(2026, 7, 10)]["kind"] == "both"
    assert days[date(2026, 7, 10)]["lifting"] is True
    assert days[date(2026, 7, 10)]["cardio"] is True
    assert days[date(2026, 7, 11)]["fitbit"] is True
    assert days[date(2026, 7, 11)]["kind"] == "cardio"
    # Strength-typed AH with no Hevy that day still counts as lifting.
    assert days[date(2026, 7, 12)]["lifting"] is True
    assert days[date(2026, 7, 12)]["kind"] == "lifting"


def test_strength_like_cardio_dropped_when_hevy_same_day():
    strength = [{"event_date": "2026-07-10"}]
    cardio = [
        {
            "event_date": "2026-07-10",
            "activity_type": "Traditional Strength Training",
            "source_app": "Apple Watch",
        }
    ]
    days = build_workout_day_map(strength, cardio)
    assert days[date(2026, 7, 10)]["lifting"] is True
    assert days[date(2026, 7, 10)]["cardio"] is False
    assert "Traditional Strength Training" not in days[date(2026, 7, 10)]["activity_types"]


def test_compute_streaks_current_and_longest():
    # Worked Jul 8–10 and Jul 12–14; as_of Jul 14 → current 3, longest 3.
    active = {
        date(2026, 7, 8): {},
        date(2026, 7, 9): {},
        date(2026, 7, 10): {},
        date(2026, 7, 12): {},
        date(2026, 7, 13): {},
        date(2026, 7, 14): {},
    }
    streaks = compute_streaks(active, as_of=date(2026, 7, 14))
    assert streaks["current_streak"] == 3
    assert streaks["longest_streak"] == 3
    assert streaks["workout_days_count"] == 6


def test_compute_streaks_grace_when_today_empty():
    active = {
        date(2026, 7, 12): {},
        date(2026, 7, 13): {},
    }
    # as_of Jul 14 has no workout yet — streak still counts through yesterday.
    streaks = compute_streaks(active, as_of=date(2026, 7, 14))
    assert streaks["current_streak"] == 2


def test_month_bounds_and_previous_month():
    assert month_bounds(2026, 7) == (date(2026, 7, 1), date(2026, 7, 31))
    assert month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert previous_month(2026, 1) == (2025, 12)
    assert previous_month(2026, 7) == (2026, 6)


def test_build_month_grid_marks_workout_days():
    day_map = {
        date(2026, 7, 10): {
            "lifting": True,
            "cardio": False,
            "fitbit": False,
            "kind": "lifting",
            "activity_types": [],
            "sources": ["hevy"],
        }
    }
    grid = build_month_grid(day_map, year=2026, month=7, as_of=date(2026, 7, 15))
    cell = next(c for c in grid if c["date"] == date(2026, 7, 10))
    assert cell["worked_out"] is True
    assert cell["kind"] == "lifting"
    assert "Lifting" in cell["activity_types"]
    future = next(c for c in grid if c["date"] == date(2026, 7, 20))
    assert future["future"] is True
    assert future["worked_out"] is False


def test_build_workout_calendar_payload_shape():
    strength = [{"event_date": "2026-07-10"}]
    cardio = [
        {
            "event_date": "2026-07-11",
            "activity_type": "Walking",
            "source_app": "Fitbit",
        }
    ]
    payload = build_workout_calendar(
        strength,
        cardio,
        as_of=date(2026, 7, 15),
        include_previous_month=True,
    )
    assert payload["as_of"] == "2026-07-15"
    assert payload["current_streak"] == 0  # gap after Jul 11
    assert "2026-07-10" in payload["days"]
    assert "2026-07-11" in payload["days"]
    assert payload["days"]["2026-07-11"]["fitbit"] is True
    assert len(payload["months"]) == 2
    assert payload["months"][0]["label"] == "June 2026"
    assert payload["months"][1]["label"] == "July 2026"
    july = payload["months"][1]
    assert july["workout_day_count"] == 2

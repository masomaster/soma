"""Tests for calendar-week workload pace indicators and RYG status."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.strength_analytics import is_hard_set
from pipeline.workload_pace import (
    build_workload_pace_summary,
    calendar_week_cardio_load,
    calendar_week_strength_load_lbs,
    pace_status_message,
)


def _strength_row(*, event_date: date, reps: int, weight_lbs: float) -> dict:
    return {
        "event_date": event_date,
        "exercise_name": "Squat",
        "set_type": "working" if is_hard_set("working") else "warmup",
        "reps": reps,
        "weight_lbs": weight_lbs,
    }


def _cardio_row(
    *,
    event_date: date,
    activity_type: str,
    duration_min: float,
    distance_miles: float | None = None,
) -> dict:
    row = {
        "event_date": event_date,
        "activity_type": activity_type,
        "duration_min": duration_min,
    }
    if distance_miles is not None:
        row["distance_miles"] = distance_miles
    return row


def test_calendar_week_strength_load_sums_working_sets():
    week_start = date(2024, 6, 3)  # Monday
    events = [
        _strength_row(event_date=week_start, reps=5, weight_lbs=200),
        _strength_row(event_date=week_start + timedelta(days=1), reps=5, weight_lbs=100),
    ]
    assert calendar_week_strength_load_lbs(events, week_start=week_start) == 1500.0


def test_build_workload_pace_summary_red_on_strength_spike():
    anchor = date(2024, 6, 10)  # Monday
    events: list[dict] = []
    # Four stable weeks at ~10k lb
    for w in range(4):
        week_start = anchor - timedelta(days=7 * (4 - w))
        events.append(_strength_row(event_date=week_start, reps=10, weight_lbs=100))
    # Current week spike to 20k lb (100% jump)
    events.append(_strength_row(event_date=anchor, reps=20, weight_lbs=100))
    summary = build_workload_pace_summary(strength_events=events, cardio_events=[], as_of=anchor)
    lifting = summary["lifting"]
    assert lifting["status"] in ("yellow", "red")
    assert lifting["wow_change_pct"] is not None
    assert lifting["wow_change_pct"] >= 50


def test_build_workload_pace_summary_cardio_acwr_green_zone():
    anchor = date(2024, 6, 10)
    events: list[dict] = []
    for w in range(5):
        week_start = anchor - timedelta(days=7 * (4 - w))
        events.append(
            _cardio_row(
                event_date=week_start,
                activity_type="Run",
                duration_min=60,
                distance_miles=5.0,
            )
        )
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=anchor)
    cardio = summary["cardio"]
    assert cardio["status"] == "green"
    assert cardio["acwr"] is not None
    assert 0.8 <= cardio["acwr"] <= 1.3


def test_running_and_cycling_weekly_rollups_present():
    anchor = date(2024, 6, 10)
    events = [
        _cardio_row(event_date=anchor, activity_type="Run", duration_min=40, distance_miles=4.0),
        _cardio_row(event_date=anchor, activity_type="Ride", duration_min=50, distance_miles=12.0),
    ]
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=anchor)
    assert summary["running"]["weekly_rollups"]
    assert summary["cycling"]["weekly_rollups"]
    assert summary["running"]["weekly_rollups"][-1]["load"] == 4.0
    assert summary["cycling"]["weekly_rollups"][-1]["load"] == 12.0


def test_pace_status_message_includes_emoji():
    domain = {
        "status": "yellow",
        "emoji": "🟡",
        "label": "Cautious — ease up a little",
        "acwr": 1.35,
        "wow_change_pct": 12.0,
        "vs_monthly_avg_pct": 18.0,
    }
    msg = pace_status_message(domain)
    assert "🟡" in msg
    assert "ACWR" in msg
    assert "WoW" in msg

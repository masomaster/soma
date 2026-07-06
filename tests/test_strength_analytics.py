"""Tests for per-exercise strength analytics."""

from __future__ import annotations

from datetime import date

from pipeline.strength_analytics import (
    build_strength_progress_summary,
    detect_strength_progress_flags,
    infer_session_focus,
    top_exercises_summary,
    weekly_strength_rollups,
)


def _event(
    *,
    event_date: date,
    exercise_name: str,
    reps: int,
    weight_lbs: float,
    set_type: str = "working",
) -> dict:
    return {
        "event_date": event_date.isoformat(),
        "exercise_name": exercise_name,
        "set_type": set_type,
        "reps": reps,
        "weight_lbs": weight_lbs,
    }


def test_infer_session_focus_upper_and_lower():
    assert infer_session_focus("Bench Press (Dumbbell)") == "upper"
    assert infer_session_focus("Squat (Barbell)") == "lower"


def test_top_exercises_summary_tracks_weight_progression():
    events = [
        _event(event_date=date(2024, 6, 1), exercise_name="Bench Press (Dumbbell)", reps=8, weight_lbs=140),
        _event(event_date=date(2024, 6, 8), exercise_name="Bench Press (Dumbbell)", reps=8, weight_lbs=145),
    ]
    summary = top_exercises_summary(events, as_of=date(2024, 6, 8))
    assert len(summary) == 1
    assert summary[0]["latest_top_weight_lbs"] == 145
    assert summary[0]["weight_delta_vs_prior"] == 5.0


def test_weekly_rollups_include_week_over_week_change():
    events = [
        _event(event_date=date(2024, 6, 3), exercise_name="Squat (Barbell)", reps=5, weight_lbs=200),
        _event(event_date=date(2024, 6, 10), exercise_name="Squat (Barbell)", reps=5, weight_lbs=200),
        _event(event_date=date(2024, 6, 10), exercise_name="Squat (Barbell)", reps=5, weight_lbs=200),
    ]
    rows = weekly_strength_rollups(events, as_of=date(2024, 6, 12), weeks=2)
    assert len(rows) == 2
    assert rows[-1]["change_pct"] is not None
    assert rows[-1]["change_pct"] > 0


def test_detect_rapid_volume_increase_flag():
    weekly = [
        {"volume_lbs": 10000, "change_pct": None},
        {"volume_lbs": 12000, "change_pct": 20.0},
    ]
    flags = detect_strength_progress_flags(weekly, rapid_increase_pct=12.0)
    assert any(f["code"] == "STRENGTH_VOLUME_SPIKE" for f in flags)


def test_build_strength_progress_summary_keys():
    events = [
        _event(event_date=date(2024, 6, 3), exercise_name="Bicep Curl (Dumbbell)", reps=10, weight_lbs=30),
    ]
    summary = build_strength_progress_summary(events, as_of=date(2024, 6, 8))
    assert "weekly_rollups" in summary
    assert "top_exercises" in summary
    assert "exercise_series" in summary

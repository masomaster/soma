"""Tests for Slice A goal progress and today's focus."""

from __future__ import annotations

from datetime import date

from pipeline.goal_progress import (
    build_daily_goal_snapshot,
    compute_goal_status,
    suggest_todays_focus,
)

RUN = date(2024, 6, 6)  # Thursday


def _goals():
    return [
        {
            "goal_type": "strength",
            "target_min": 3,
            "target_max": 4,
            "target_label": "3-4x",
            "is_active": True,
        },
        {"goal_type": "running_interval", "is_active": True, "target_min": 1},
        {"goal_type": "running_easy", "is_active": True, "target_min": 1},
    ]


def test_compute_goal_status_strength_behind_on_thursday():
    strength = [{"event_date": RUN - __import__("datetime").timedelta(days=1)}]
    status = compute_goal_status(
        run_date=RUN,
        goals=_goals(),
        strength_events=strength,
        running_sessions=[],
    )
    assert status["strength"]["completed"] == 1
    assert status["strength"]["status"] in ("behind", "urgent")


def test_running_easy_done_from_session():
    status = compute_goal_status(
        run_date=RUN,
        goals=_goals(),
        strength_events=[],
        running_sessions=[
            {"session_date": RUN - __import__("datetime").timedelta(days=2), "run_type": "easy"}
        ],
    )
    assert status["running"]["easy"]["done"] is True
    assert status["running"]["easy"]["status"] == "done"


def test_suggest_todays_focus_includes_pending():
    goals_status = {
        "strength": {"completed": 1, "target": "3-4x", "status": "behind"},
        "running": {"interval": {"done": False, "status": "not_yet"}},
    }
    focus = suggest_todays_focus(goals_status=goals_status, run_date=RUN)
    assert "Strength" in focus
    assert "Interval" in focus


def test_build_daily_goal_snapshot_shape():
    snap = build_daily_goal_snapshot(
        user_id="u1",
        run_date=RUN,
        goals=_goals(),
        strength_events=[],
        running_sessions=[],
    )
    assert snap["user_id"] == "u1"
    assert "goals_status" in snap
    assert "mileage_check" in snap
    assert isinstance(snap["todays_focus"], str)

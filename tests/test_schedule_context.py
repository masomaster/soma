"""Tests for Slice D schedule context."""

from __future__ import annotations

from datetime import date

from pipeline.goal_progress import suggest_todays_focus as focus_from_goals
from pipeline.schedule_context import is_goal_blocked


def test_goal_blocked_by_exception():
    blocked = is_goal_blocked(
        "running_interval",
        run_date=date(2024, 6, 10),
        exceptions=[
            {
                "start_date": date(2024, 6, 9),
                "end_date": date(2024, 6, 11),
                "affected_goal_types": ["running_interval"],
                "override_hint": "Skip intervals — travel",
            }
        ],
    )
    assert blocked == "Skip intervals — travel"


def test_focus_includes_schedule_hint():
    goals_status = {
        "running": {
            "interval": {
                "done": False,
                "status": "skipped",
                "schedule_note": "Skip intervals — travel",
            }
        }
    }
    focus = focus_from_goals(
        goals_status=goals_status,
        run_date=date(2024, 6, 10),
    )
    assert "skipped" in focus.lower() or "Skip" in focus

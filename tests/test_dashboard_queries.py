"""Tests for Phase 9 dashboard queries."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.dashboard_queries import build_dashboard_context, validate_bounded_sql


def test_build_dashboard_context_keys():
    ctx = build_dashboard_context(
        user_id="u1",
        as_of=date(2024, 6, 8),
        latest_briefing={"briefing_date": date(2024, 6, 8), "coaching_note": "Hi"},
        latest_features={"feature_date": date(2024, 6, 8), "strength_sessions_7d": 2},
        latest_metrics=None,
        goal_snapshot={"todays_focus": "Lift"},
        weekly_summary=None,
    )
    assert ctx["user_id"] == "u1"
    assert ctx["briefing"]["coaching_note"] == "Hi"
    assert ctx["todays_focus"] == "Lift"


def test_validate_bounded_sql_rejects_insert():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_bounded_sql("INSERT INTO goals VALUES (1)", user_id="abc")


def test_validate_bounded_sql_accepts_select_with_user():
    sql = validate_bounded_sql(
        "SELECT * FROM daily_features WHERE user_id = 'abc-123'",
        user_id="abc-123",
    )
    assert sql.startswith("SELECT")

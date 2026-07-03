"""Tests for Phase 9 dashboard queries."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.dashboard_queries import (
    build_dashboard_context,
    fetch_dashboard_source_rows,
    validate_bounded_sql,
)


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


def test_fetch_dashboard_source_rows_from_injected_queries():
    def query_one(sql: str, params: tuple) -> dict | None:
        if "daily_briefings" in sql:
            return {
                "briefing_date": date(2024, 6, 8),
                "coaching_note": "Go easy",
                "flags": ["LOW_HRV"],
            }
        if "daily_goal_snapshot" in sql:
            return {
                "snapshot_date": date(2024, 6, 8),
                "goals_status": {"strength": {"status": "on_track"}},
                "mileage_check": {"flag": None},
                "todays_focus": "Easy run",
            }
        return None

    def query_all(sql: str, params: tuple) -> list[dict]:
        if "provider_connections" in sql:
            return [{"provider": "hevy", "status": "connected", "last_sync_at": None, "last_error": None}]
        return []

    ctx = fetch_dashboard_source_rows(
        user_id="u1",
        as_of=date(2024, 6, 8),
        query_one=query_one,
        query_all=query_all,
    )
    assert ctx["briefing"]["coaching_note"] == "Go easy"
    assert ctx["todays_focus"] == "Easy run"
    assert ctx["sync_health"][0]["provider"] == "hevy"


def test_build_dashboard_context_weekly_summary_json_string():
    ctx = build_dashboard_context(
        user_id="u1",
        as_of=date(2024, 6, 8),
        latest_briefing=None,
        latest_features=None,
        latest_metrics=None,
        goal_snapshot=None,
        weekly_summary={
            "week_start": date(2024, 6, 3),
            "strength_sessions": 2,
            "running_km": 5.0,
            "cardio_minutes": 40,
            "summary_json": '{"strength_short_tons": 1.5, "strength_hard_sets": 12}',
        },
    )
    assert ctx["weekly_summary"]["strength_short_tons"] == 1.5
    assert ctx["weekly_summary"]["strength_hard_sets"] == 12

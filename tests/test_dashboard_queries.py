"""Tests for Phase 9 dashboard queries."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.dashboard_queries import (
    MAX_HISTORY_DAYS,
    MAX_QUERY_ROWS,
    build_dashboard_context,
    cardio_mode,
    fetch_dashboard_source_rows,
    fetch_features_history,
    fetch_metrics_history,
    fetch_weekly_summaries,
    summarize_cardio_by_mode,
    validate_bounded_sql,
)
from pipeline.units import km_to_miles


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


def test_context_converts_mileage_and_weekly_to_miles():
    ctx = build_dashboard_context(
        user_id="u1",
        as_of=date(2024, 6, 8),
        latest_briefing=None,
        latest_features=None,
        latest_metrics=None,
        goal_snapshot={"mileage_check": {"this_week_km": 10.0, "last_week_km": 5.0, "flag": None}},
        weekly_summary={"week_start": date(2024, 6, 3), "running_km": 10.0, "cardio_minutes": 60},
    )
    assert ctx["mileage_check"]["this_week_miles"] == pytest.approx(km_to_miles(10.0))
    assert ctx["mileage_check"]["last_week_miles"] == pytest.approx(km_to_miles(5.0))
    assert "this_week_km" not in ctx["mileage_check"]
    assert ctx["weekly_summary"]["running_miles"] == pytest.approx(km_to_miles(10.0))
    assert "running_km" not in ctx["weekly_summary"]


def test_cardio_mode_classifies_run_and_bike():
    assert cardio_mode("Outdoor Run") == "running"
    assert cardio_mode("Outdoor Cycling") == "cycling"
    assert cardio_mode("Ride") == "cycling"
    assert cardio_mode("Rowing") == "other"


def test_summarize_cardio_by_mode_separates_running_and_cycling():
    events = [
        {"activity_type": "Outdoor Run", "distance_miles": 3.0, "duration_min": 27.0},
        {"activity_type": "Outdoor Cycling", "distance_miles": 12.0, "duration_min": 45.0},
        # Over-recorded run (1:40/mi) — distance excluded, but session/time still counted.
        {"activity_type": "Run", "distance_miles": 3.0, "duration_min": 5.0},
    ]
    totals = summarize_cardio_by_mode(events)
    assert totals["running"]["miles"] == pytest.approx(3.0)
    assert totals["running"]["sessions"] == 2
    assert totals["cycling"]["miles"] == pytest.approx(12.0)
    assert totals["cycling"]["sessions"] == 1


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
        return []

    ctx = fetch_dashboard_source_rows(
        user_id="u1",
        as_of=date(2024, 6, 8),
        query_one=query_one,
        query_all=query_all,
    )
    assert ctx["briefing"]["coaching_note"] == "Go easy"
    assert ctx["todays_focus"] == "Easy run"
    # Sync-health surfacing was removed; provider status is no longer in context.
    assert "sync_health" not in ctx


def test_build_dashboard_context_includes_correlations():
    ctx = build_dashboard_context(
        user_id="u1",
        as_of=date(2024, 6, 8),
        latest_briefing=None,
        latest_features=None,
        latest_metrics=None,
        goal_snapshot=None,
        weekly_summary=None,
        metric_patterns=[
            {
                "metric_a": "sleep_hours",
                "metric_b": "strength_tonnage_7d",
                "lag_days": 1,
                "correlation": 0.72,
                "sample_n": 21,
                "status": "active",
                "description": "sleep hours vs 7d strength tonnage (lag 1d): r=0.72 (positive, n=21)",
            },
            {
                "metric_a": "sleep_hours",
                "metric_b": "resting_hr",
                "lag_days": 0,
                "correlation": -0.55,
                "sample_n": 30,
                "status": "active",
                "description": "sleep hours vs resting HR (lag 0d): r=-0.55 (negative, n=30)",
            },
        ],
    )
    corrs = ctx["correlations"]
    assert len(corrs) == 2
    strength = next(c for c in corrs if c["metric_b"] == "strength_tonnage_7d")
    assert strength["direction"] == "positive"
    assert strength["correlation"] == 0.72
    assert strength["lag_days"] == 1
    assert strength["sample_n"] == 21
    rhr = next(c for c in corrs if c["metric_b"] == "resting_hr")
    assert rhr["direction"] == "negative"


def test_build_dashboard_context_omits_empty_correlations():
    ctx = build_dashboard_context(
        user_id="u1",
        as_of=date(2024, 6, 8),
        latest_briefing=None,
        latest_features=None,
        latest_metrics=None,
        goal_snapshot=None,
        weekly_summary=None,
        metric_patterns=[],
    )
    assert "correlations" not in ctx


def test_fetch_dashboard_source_rows_surfaces_metric_patterns():
    def query_one(sql: str, params: tuple) -> dict | None:
        return None

    def query_all(sql: str, params: tuple) -> list[dict]:
        if "metric_patterns" in sql:
            return [
                {
                    "metric_a": "sleep_hours",
                    "metric_b": "cardio_minutes_7d",
                    "lag_days": 0,
                    "correlation": 0.63,
                    "sample_n": 18,
                    "status": "active",
                    "description": "sleep hours vs 7d cardio minutes (lag 0d): r=0.63 (positive, n=18)",
                }
            ]
        return []

    ctx = fetch_dashboard_source_rows(
        user_id="u1",
        as_of=date(2024, 6, 8),
        query_one=query_one,
        query_all=query_all,
    )
    assert ctx["correlations"][0]["metric_b"] == "cardio_minutes_7d"
    assert ctx["correlations"][0]["direction"] == "positive"


class _FakeCursor:
    """Minimal psycopg2-style cursor that records the executed SQL + params."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[dict]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self.cur = _FakeCursor(rows)

    def cursor(self, cursor_factory: object = None) -> _FakeCursor:
        return self.cur


def test_fetch_metrics_history_is_bounded_and_user_scoped():
    rows = [{"metric_date": date(2024, 6, 1), "hrv_rmssd": 50}]
    conn = _FakeConn(rows)
    out = fetch_metrics_history(conn, user_id="u1", as_of=date(2024, 6, 8), days=30)
    assert out == rows
    sql, params = conn.cur.executed[0]
    assert "FROM daily_health_metrics" in sql
    assert "user_id = %s" in sql
    assert "ORDER BY metric_date ASC" in sql
    assert f"LIMIT {MAX_QUERY_ROWS}" in sql
    # window: [as_of - (days-1), as_of]; user_id is the first bound parameter.
    assert params == ("u1", date(2024, 5, 10), date(2024, 6, 8))


def test_fetch_metrics_history_clamps_excessive_range():
    conn = _FakeConn([])
    fetch_metrics_history(conn, user_id="u1", as_of=date(2024, 6, 8), days=99999)
    _, params = conn.cur.executed[0]
    span = (params[2] - params[1]).days
    assert span == MAX_HISTORY_DAYS - 1


def test_fetch_features_and_weekly_history_target_correct_tables():
    fconn = _FakeConn([])
    fetch_features_history(fconn, user_id="u1", as_of=date(2024, 6, 8), days=14)
    assert "FROM daily_features" in fconn.cur.executed[0][0]

    wconn = _FakeConn([])
    fetch_weekly_summaries(wconn, user_id="u1", as_of=date(2024, 6, 8), weeks=8)
    assert "FROM weekly_activity_summary" in wconn.cur.executed[0][0]


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

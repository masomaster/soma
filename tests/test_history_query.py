"""Tests for Slice C.1 history query (text-to-SQL)."""

from __future__ import annotations

import pytest

from pipeline.history_query import (
    extract_sql_from_llm,
    generate_bounded_sql,
    run_history_query,
)
from pipeline.dashboard_queries import validate_bounded_sql


UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_extract_sql_from_markdown_fence():
    raw = "Here:\n```sql\nSELECT 1\n```"
    assert extract_sql_from_llm(raw).upper().startswith("SELECT")


def test_validate_bounded_sql_rejects_bad_table():
    sql = (
        f"SELECT * FROM secret_table WHERE user_id = '{UID}' LIMIT 10"
    )
    with pytest.raises(ValueError, match="not allowed"):
        validate_bounded_sql(sql, user_id=UID)


def test_generate_bounded_sql_mock_llm():
    question = "Average sleep last month?"

    def fake_llm(system: str, prompt: str) -> str:
        del system, prompt
        return (
            f"SELECT AVG(sleep_hours) FROM daily_health_metrics "
            f"WHERE user_id = '{UID}' LIMIT 100"
        )

    sql = generate_bounded_sql(question, user_id=UID, llm=fake_llm)
    assert "daily_health_metrics" in sql
    assert UID in sql


def test_run_history_query_success():
    def fake_llm(system: str, prompt: str) -> str:
        del system, prompt
        return (
            f"SELECT metric_date, sleep_hours FROM daily_health_metrics "
            f"WHERE user_id = '{UID}' LIMIT 5"
        )

    def query_all(sql: str, params: tuple) -> list[dict]:
        assert UID in sql
        return [{"metric_date": "2026-06-01", "sleep_hours": 7.0}]

    out = run_history_query(
        "sleep trend?",
        user_id=UID,
        llm=fake_llm,
        query_all=query_all,
    )
    assert out["ok"] is True
    assert out["row_count"] == 1

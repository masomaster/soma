"""Adversarial tests for the Slice C bounded text-to-SQL guard.

These assert that ``validate_bounded_sql`` rejects cross-tenant and side-effecting
query shapes the security review flagged (OR/UNION/CROSS JOIN/subquery/CTE/
file functions), and accepts a normal single-user aggregate.
"""

from __future__ import annotations

import pytest

from pipeline.dashboard_queries import MAX_QUERY_ROWS, validate_bounded_sql

UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER = "11111111-2222-3333-4444-555555555555"


def _q(sql: str) -> str:
    return validate_bounded_sql(sql, user_id=UID)


def test_accepts_single_user_aggregate():
    sql = _q(
        f"SELECT metric_date, AVG(sleep_hours) AS s FROM daily_health_metrics "
        f"WHERE user_id = '{UID}' GROUP BY metric_date ORDER BY metric_date DESC LIMIT 30"
    )
    assert sql.upper().startswith("SELECT")
    assert UID in sql
    assert "daily_health_metrics" in sql


def test_appends_limit_when_missing():
    sql = _q(f"SELECT sleep_hours FROM daily_health_metrics WHERE user_id = '{UID}'")
    assert f"LIMIT {MAX_QUERY_ROWS}" in sql.upper()


def test_clamps_oversized_limit():
    sql = _q(
        f"SELECT sleep_hours FROM daily_health_metrics WHERE user_id = '{UID}' LIMIT 100000"
    )
    assert f"LIMIT {MAX_QUERY_ROWS}" in sql.upper()
    assert "100000" not in sql


def test_rejects_or_tautology():
    with pytest.raises(ValueError, match="OR conditions"):
        _q(
            f"SELECT * FROM daily_features WHERE user_id = '{UID}' OR 1=1 LIMIT 10"
        )


def test_rejects_union_to_other_user():
    with pytest.raises(ValueError, match="Set operations"):
        _q(
            f"SELECT * FROM daily_features WHERE user_id = '{UID}' "
            f"UNION ALL SELECT * FROM daily_features WHERE user_id = '{OTHER}'"
        )


def test_rejects_cross_join():
    with pytest.raises(ValueError, match="JOIN"):
        _q(
            f"SELECT v.* FROM daily_features a CROSS JOIN daily_features v "
            f"WHERE a.user_id = '{UID}' LIMIT 10"
        )


def test_rejects_subquery():
    with pytest.raises(ValueError, match="Subqueries"):
        _q(
            f"SELECT * FROM daily_features WHERE user_id = '{UID}' "
            f"AND feature_date IN (SELECT feature_date FROM daily_features) LIMIT 10"
        )


def test_rejects_cte():
    with pytest.raises(ValueError, match="CTEs"):
        _q(
            f"WITH x AS (SELECT * FROM daily_features) "
            f"SELECT * FROM x WHERE user_id = '{UID}' LIMIT 10"
        )


def test_rejects_user_id_only_in_projection_not_where():
    # EQ is present but in the SELECT list, not filtering rows.
    with pytest.raises(ValueError, match="filter by user_id"):
        _q(f"SELECT (user_id = '{UID}') AS mine FROM daily_features LIMIT 10")


def test_rejects_missing_user_id():
    with pytest.raises(ValueError, match="filter by user_id"):
        _q("SELECT * FROM daily_features LIMIT 10")


def test_rejects_wrong_user_id():
    with pytest.raises(ValueError, match="filter by user_id"):
        _q(f"SELECT * FROM daily_features WHERE user_id = '{OTHER}' LIMIT 10")


def test_rejects_non_allowlisted_table():
    with pytest.raises(ValueError, match="not allowed"):
        _q(f"SELECT * FROM auth_users WHERE user_id = '{UID}' LIMIT 10")


def test_rejects_file_read_function():
    with pytest.raises(ValueError, match="not allowed"):
        _q(
            f"SELECT pg_read_file('/etc/passwd') FROM daily_features "
            f"WHERE user_id = '{UID}' LIMIT 1"
        )


def test_rejects_comma_join_same_table():
    with pytest.raises(ValueError):
        _q(
            f"SELECT a.* FROM daily_features a, daily_features b "
            f"WHERE a.user_id = '{UID}' LIMIT 10"
        )


def test_rejects_table_function_in_from():
    with pytest.raises(ValueError):
        _q(f"SELECT * FROM pg_read_file('/etc/passwd') WHERE user_id = '{UID}' LIMIT 10")


def test_rejects_non_select():
    with pytest.raises(ValueError, match="Only SELECT"):
        _q(f"UPDATE goals SET is_active = false WHERE user_id = '{UID}'")


def test_rejects_stacked_statement():
    with pytest.raises(ValueError):
        _q(
            f"SELECT * FROM daily_features WHERE user_id = '{UID}'; "
            f"DROP TABLE daily_features"
        )

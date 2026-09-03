"""Ingest-time biometrics → daily_health_metrics rollup (dashboard path)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from pipeline.biometrics_daily_rollup import (
    event_dates_from_biometrics_rows,
    rollup_biometrics_dates,
)


_USER = "00000000-0000-0000-0000-000000000001"


def test_event_dates_from_biometrics_rows_unique_sorted() -> None:
    rows = [
        {"event_date": date(2026, 8, 2), "metric": "steps"},
        {"event_date": "2026-08-01", "metric": "sleep_hours"},
        {"event_date": date(2026, 8, 2), "metric": "sleep_hours"},
    ]
    assert event_dates_from_biometrics_rows(rows) == [
        date(2026, 8, 1),
        date(2026, 8, 2),
    ]


def test_rollup_biometrics_dates_upserts_wide_row_with_sleep() -> None:
    cur = MagicMock()
    bio_rows = [("sleep_hours", 7.5), ("sleep_deep_hrs", 1.5), ("steps", 4000.0)]

    def fetchall_side_effect() -> list:
        sql = str(cur.execute.call_args[0][0])
        if "FROM biometrics" in sql:
            return list(bio_rows)
        return []

    cur.fetchall.side_effect = fetchall_side_effect
    cur.description = [("metric_date",), ("hrv_rmssd",), ("resting_hr",)]

    with patch("pipeline.biometrics_daily_rollup.upsert_row") as upsert:
        out = rollup_biometrics_dates(
            cur, user_id=_USER, dates=[date(2026, 8, 27)]
        )
    assert len(out) == 1
    assert out[0]["metric_date"] == date(2026, 8, 27)
    assert out[0]["sleep_hours"] == 7.5
    assert out[0]["sleep_deep_hrs"] == 1.5
    assert out[0]["steps"] == 4000
    assert out[0].get("sleep_score") is not None
    upsert.assert_called_once()
    assert upsert.call_args.args[1] == "daily_health_metrics"
    assert upsert.call_args.args[2]["sleep_hours"] == 7.5


def test_rollup_biometrics_dates_skips_empty_day() -> None:
    cur = MagicMock()
    cur.fetchall.return_value = []
    with patch("pipeline.biometrics_daily_rollup.upsert_row") as upsert:
        out = rollup_biometrics_dates(cur, user_id=_USER, dates=[date(2026, 9, 1)])
    assert out == []
    upsert.assert_not_called()

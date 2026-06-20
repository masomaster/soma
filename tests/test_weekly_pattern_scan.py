"""Tests for :mod:`pipeline.weekly_pattern_scan` (offline, injected LLM)."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.weekly_pattern_scan import (
    build_llm_pattern_anomaly_rows,
    build_weekly_summary_payload,
    run_weekly_pattern_scan,
    weekly_scan_enabled,
)

SUNDAY = date(2024, 6, 16)
MONDAY = date(2024, 6, 17)


def _metrics(run: date, days: int = 20) -> list[dict]:
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = run - timedelta(days=i)
        rows.append(
            {
                "metric_date": d,
                "hrv_rmssd": 48.0 + (i % 3),
                "sleep_hours": 7.0,
                "resting_hr": 58,
            }
        )
    return rows


def test_weekly_scan_enabled_truthy_values():
    assert weekly_scan_enabled("1")
    assert weekly_scan_enabled("true")
    assert not weekly_scan_enabled(None)
    assert not weekly_scan_enabled("0")


def test_build_weekly_summary_filters_window():
    history = _metrics(SUNDAY, days=30)
    payload = build_weekly_summary_payload(history, run_date=SUNDAY, lookback_days=14)
    assert len(payload["days"]) <= 14
    assert payload["as_of"] == SUNDAY.isoformat()


def test_run_weekly_pattern_scan_skips_non_sunday():
    out = run_weekly_pattern_scan(
        user_id="u1",
        run_date=MONDAY,
        daily_metrics=_metrics(MONDAY),
        llm=lambda s, u: "[]",
    )
    assert out is None


def test_run_weekly_pattern_scan_parses_json_array():
    def llm(_system: str, _user: str) -> str:
        return '[{"title": "Sleep lag", "description": "Short sleep often precedes low HRV.", "confidence": "low"}]'

    out = run_weekly_pattern_scan(
        user_id="u1",
        run_date=SUNDAY,
        daily_metrics=_metrics(SUNDAY),
        llm=llm,
    )
    assert out is not None
    assert len(out) == 1
    assert out[0]["title"] == "Sleep lag"


def test_build_llm_pattern_anomaly_rows():
    rows = build_llm_pattern_anomaly_rows(
        user_id="u1",
        detected_date=SUNDAY,
        patterns=[{"title": "T", "description": "D", "confidence": "low"}],
        model_used="claude-sonnet-test",
    )
    assert rows[0]["anomaly_type"] == "llm_pattern"
    assert rows[0]["context_json"]["model_used"] == "claude-sonnet-test"

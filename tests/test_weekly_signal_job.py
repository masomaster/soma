"""Weekly signal job wiring: cross-table patterns are computed and persisted."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.weekly_signal_job import run_weekly_signal_job

_USER = "u1"
AS_OF = date(2024, 6, 30)


def _metrics(days: int = 40) -> list[dict]:
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = AS_OF - timedelta(days=i)
        sleep = 6.0 + (i % 5) * 0.3
        rows.append({"metric_date": d, "sleep_hours": sleep, "hrv_rmssd": 40.0 + sleep})
    return rows


def _features(days: int = 40) -> list[dict]:
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = AS_OF - timedelta(days=i)
        sleep = 6.0 + (i % 5) * 0.3
        rows.append(
            {
                "feature_date": d,
                "strength_tonnage_7d": 2.0 + sleep * 1.5,
                "cardio_minutes_7d": 20.0 + sleep * 4.0,
            }
        )
    return rows


def test_weekly_job_persists_cross_metric_patterns():
    persisted: list[dict] = []
    result = run_weekly_signal_job(
        user_id=_USER,
        run_date=AS_OF,
        daily_metrics_window=_metrics(),
        daily_features_window=_features(),
        persist_patterns=lambda rows: persisted.extend(rows),
    )
    assert result["ok"] is True
    assert result["patterns"] == len(persisted)
    assert any(p["metric_b"] == "strength_tonnage_7d" for p in persisted)
    assert any(p["metric_b"] == "cardio_minutes_7d" for p in persisted)


def test_weekly_job_without_features_still_runs():
    persisted: list[dict] = []
    result = run_weekly_signal_job(
        user_id=_USER,
        run_date=AS_OF,
        daily_metrics_window=_metrics(),
        persist_patterns=lambda rows: persisted.extend(rows),
    )
    assert result["ok"] is True
    assert all(p["metric_b"] in {"hrv_rmssd", "resting_hr", "active_cal"} for p in persisted)

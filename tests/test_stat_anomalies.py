"""Tests for :mod:`pipeline.stat_anomalies` (z-score signals, no network)."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.stat_anomalies import compute_statistical_signals

RUN = date(2024, 6, 15)


def _history_with_jitter(*, run: date, days: int = 20) -> list[dict]:
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = run - timedelta(days=i)
        hrv = 50.0 + (i % 5) * 0.35
        rows.append({"metric_date": d, "sleep_hours": 6.0, "hrv_rmssd": hrv, "resting_hr": 58})
    return rows


def test_insufficient_baseline_returns_empty_anomalies():
    history = _history_with_jitter(run=RUN, days=10)
    out = compute_statistical_signals(
        feature_date=RUN,
        daily_metrics_history=history,
        today_metrics={"hrv_rmssd": 20.0, "sleep_hours": 6.0},
        min_baseline_days=14,
    )
    assert out["anomalies"] == []
    assert out["trends"] == []


def test_z_score_flags_large_negative_hrv_move():
    history = _history_with_jitter(run=RUN, days=20)
    out = compute_statistical_signals(
        feature_date=RUN,
        daily_metrics_history=history,
        today_metrics={"hrv_rmssd": 28.0, "sleep_hours": 6.0, "resting_hr": 58},
        z_threshold=2.0,
        min_baseline_days=14,
    )
    hrv_flags = [a for a in out["anomalies"] if a["metric"] == "hrv_rmssd"]
    assert len(hrv_flags) == 1
    assert hrv_flags[0]["direction"] == "below_baseline"
    assert hrv_flags[0]["z_score"] < -2.0
    assert hrv_flags[0]["baseline_n"] >= 14


def test_skips_when_today_metric_missing():
    history = _history_with_jitter(run=RUN, days=20)
    out = compute_statistical_signals(
        feature_date=RUN,
        daily_metrics_history=history,
        today_metrics={"sleep_hours": 6.0},
        min_baseline_days=14,
    )
    assert all(a["metric"] != "hrv_rmssd" for a in out["anomalies"])


def test_zero_variance_baseline_skips_metric():
    rows = [
        {"metric_date": RUN - timedelta(days=i), "hrv_rmssd": 50.0, "sleep_hours": 7.0}
        for i in range(1, 21)
    ]
    out = compute_statistical_signals(
        feature_date=RUN,
        daily_metrics_history=rows,
        today_metrics={"hrv_rmssd": 20.0, "sleep_hours": 7.0},
        min_baseline_days=14,
    )
    assert not any(a["metric"] == "hrv_rmssd" for a in out["anomalies"])


def test_excludes_run_date_from_baseline():
    """Today's row must not inflate the baseline mean."""
    history = _history_with_jitter(run=RUN, days=19)
    history.append({"metric_date": RUN, "hrv_rmssd": 100.0, "sleep_hours": 6.0})
    out = compute_statistical_signals(
        feature_date=RUN,
        daily_metrics_history=history,
        today_metrics={"hrv_rmssd": 28.0, "sleep_hours": 6.0},
        min_baseline_days=14,
    )
    hrv = [a for a in out["anomalies"] if a["metric"] == "hrv_rmssd"]
    assert len(hrv) == 1
    assert abs(hrv[0]["baseline_mean"] - 50.0) < 3.0

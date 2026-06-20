"""Phase 8 metric_baselines Layer 1 tests."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.metric_baselines import compute_metric_baselines

_USER = "u1"
RUN = date(2024, 6, 15)


def test_compute_metric_baselines_emits_window_rows():
    history = [
        {
            "metric_date": RUN - timedelta(days=i),
            "hrv_rmssd": 50.0 + i * 0.1,
            "sleep_hours": 7.0,
        }
        for i in range(1, 31)
    ]
    rows = compute_metric_baselines(
        user_id=_USER,
        metric_date=RUN,
        daily_metrics_history=history,
    )
    windows = {r["window_days"] for r in rows}
    assert 7 in windows
    assert 28 in windows
    assert all(r["user_id"] == _USER for r in rows)
    assert all(r["metric_date"] == RUN for r in rows)

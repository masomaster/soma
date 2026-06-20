"""Phase 8 metric_patterns correlation job tests."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.metric_patterns import active_pattern_summaries, compute_metric_patterns

_USER = "u1"
AS_OF = date(2024, 6, 30)


def _correlated_history(days: int = 30) -> list[dict]:
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = AS_OF - timedelta(days=i)
        sleep = 5.0 + (i % 3) * 0.2
        hrv = 40.0 + sleep * 3.0
        rows.append({"metric_date": d, "sleep_hours": sleep, "hrv_rmssd": hrv})
    return rows


def test_compute_metric_patterns_finds_sleep_hrv_correlation():
    patterns = compute_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_correlated_history(),
    )
    assert patterns
    pair = next(
        (p for p in patterns if p["metric_a"] == "sleep_hours" and p["metric_b"] == "hrv_rmssd"),
        None,
    )
    assert pair is not None
    assert pair["correlation"] > 0.45
    assert pair["status"] == "active"


def test_active_pattern_summaries_filters_inactive():
    patterns = [
        {"status": "active", "description": "sleep vs hrv (lag 1d)"},
        {"status": "stale", "description": "ignored"},
    ]
    assert active_pattern_summaries(patterns) == ["sleep vs hrv (lag 1d)"]

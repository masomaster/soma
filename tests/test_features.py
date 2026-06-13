"""Phase 6 feature-computation tests (pure, offline)."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline import features as F

RUN = date(2024, 6, 8)


def _d(days_ago: int) -> date:
    return RUN - timedelta(days=days_ago)


def test_rollup_pivots_known_metrics_and_ignores_unknown():
    rows = [
        {"metric": "hrv_rmssd", "value": 48.2, "unit": "ms"},
        {"metric": "resting_hr", "value": 56, "unit": "bpm"},
        {"metric": "sleep_hours", "value": 7.25, "unit": "h"},
        {"metric": "mystery_metric", "value": 9.9},
        {"metric": "steps", "value": 8421.0},
    ]
    out = F.rollup_daily_health_metrics(rows, user_id="u1", metric_date=RUN)
    assert out["user_id"] == "u1"
    assert out["metric_date"] == RUN
    assert out["hrv_rmssd"] == 48.2
    assert out["resting_hr"] == 56 and isinstance(out["resting_hr"], int)
    assert out["sleep_hours"] == 7.25
    assert out["steps"] == 8421 and isinstance(out["steps"], int)
    assert "mystery_metric" not in out


def test_compute_daily_features_windows_and_readiness():
    daily_metrics = [
        {"metric_date": _d(i), "sleep_hours": 7.0, "hrv_rmssd": (30.0 if i == 0 else 50.0)}
        for i in range(7)
    ]
    strength = [
        {"event_date": _d(0), "set_type": "working", "reps": 5, "weight_lbs": 100},
        {"event_date": _d(0), "set_type": "working", "reps": 5, "weight_lbs": 100},
        {"event_date": _d(0), "set_type": "warmup", "reps": 10, "weight_lbs": 40},
        {"event_date": _d(2), "set_type": "working", "reps": 8, "weight_lbs": 50},
    ]
    cardio = [
        {"event_date": _d(0), "duration_min": 30},
        {"event_date": _d(3), "duration_min": 40},
        {"event_date": _d(20), "duration_min": 60},  # chronic window only
    ]

    f = F.compute_daily_features(
        user_id="u1",
        feature_date=RUN,
        strength_events=strength,
        cardio_events=cardio,
        daily_metrics=daily_metrics,
        target_sleep_hours=8.0,
        hrv_suppressed_ratio=0.85,
    )

    assert f["strength_sessions_7d"] == 2
    assert f["strength_hard_sets_7d"] == 3
    assert f["strength_tonnage_7d"] == 1400.0
    assert f["cardio_sessions_7d"] == 2
    assert f["cardio_minutes_7d"] == 70.0
    assert f["cardio_minutes_14d"] == 70.0
    # acute=70, chronic=130 over 28d -> weekly avg 32.5 -> 70/32.5
    assert f["acute_chronic_ratio"] == 2.154
    assert f["sleep_debt_7d"] == 7.0  # (8-7) * 7 days
    assert f["hrv_suppressed_days"] == 1  # only the 30ms day is below 0.85 * baseline
    # 100 - min(40, 7*4=28) - min(40, 1*8) - 20 (acwr>1.5) = 44
    assert f["overall_readiness_score"] == 44.0


def test_compute_daily_features_handles_empty_inputs():
    f = F.compute_daily_features(user_id="u1", feature_date=RUN)
    assert f["strength_sessions_7d"] == 0
    assert f["cardio_minutes_7d"] == 0.0
    assert f["acute_chronic_ratio"] is None
    assert f["sleep_debt_7d"] == 0.0
    assert f["hrv_suppressed_days"] == 0
    assert f["overall_readiness_score"] == 100.0

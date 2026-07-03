"""Native Soma sleep-score tests (pure, offline).

Covers the weighted formula in :mod:`pipeline.sleep_score`, its graceful
degradation when signals are missing, and its integration into
:func:`pipeline.features.rollup_daily_health_metrics`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import features as F
from pipeline import sleep_score as S

RUN = date(2024, 6, 8)


def _d(days_ago: int) -> date:
    return RUN - timedelta(days=days_ago)


def test_score_none_without_any_sleep_duration() -> None:
    assert S.compute_sleep_score(sleep_hours=None) is None
    assert S.compute_sleep_score(sleep_hours=0.0) is None


def test_last_night_lands_in_fair_band() -> None:
    """Regression for the real 2026-07-03 night Fitbit/Google scored 79 ("Fair").

    Previously Soma read ~84.5 (Good) because a flat 0.75 restoration constant
    plus a gentle symmetric duration curve propped a 6.9h night up. The redesign
    should land it in the Fair band, close to the ground-truth 79.
    """
    score = S.compute_sleep_score(
        sleep_hours=6.9167,
        sleep_deep_hrs=1.0167,
        sleep_rem_hrs=1.6667,
        resting_hr=58.0,
        hrv_rmssd=None,  # HRV is usually absent from HealthKit-bridged data
        resting_hr_baseline=58.0,  # ~28-day trailing mean of recent 57–60 nights
    )
    assert score is not None
    assert 77.0 <= score <= 81.0


def test_score_in_range_and_high_for_ideal_night() -> None:
    score = S.compute_sleep_score(
        sleep_hours=8.0,
        sleep_deep_hrs=8.0 * S.OPTIMAL_DEEP_FRACTION,
        sleep_rem_hrs=8.0 * S.OPTIMAL_REM_FRACTION,
        resting_hr=52.0,
        hrv_rmssd=66.0,
        hrv_baseline=60.0,
        resting_hr_baseline=56.0,
    )
    assert score is not None
    assert 0.0 <= score <= 100.0
    # On-need duration, optimal stages, better-than-baseline HRV and RHR → Excellent.
    assert score >= 90.0


def test_duration_curve_is_asymmetric() -> None:
    """A shortfall is penalised harder than an equal-sized lie-in past the need."""
    need = 8.0
    under = S.compute_sleep_score(sleep_hours=need - 2.0)  # 6h: 2h short
    over = S.compute_sleep_score(sleep_hours=need + 2.0)  # 10h: 2h long
    on_need = S.compute_sleep_score(sleep_hours=need)
    assert under is not None and over is not None and on_need is not None
    # Both deviate from need, so both sit below a perfect on-need night...
    assert under < on_need and over < on_need
    # ...but under-sleeping the same amount costs strictly more credit.
    assert under < over


def test_restoration_at_baseline_is_not_generous() -> None:
    """A resting HR merely equal to baseline yields the middling neutral value.

    It should pull a perfect-duration night down (no free credit) rather than
    leave it untouched the way the old flat-0.75 constant nearly did.
    """
    assert S.RESTORATION_NEUTRAL < 0.75
    duration_only = S.compute_sleep_score(sleep_hours=8.0)
    at_baseline = S.compute_sleep_score(
        sleep_hours=8.0, resting_hr=58.0, resting_hr_baseline=58.0
    )
    assert duration_only is not None and at_baseline is not None
    # On-need duration alone is a perfect 100; a neutral restoration term drags it down.
    assert duration_only == pytest.approx(100.0)
    assert at_baseline < duration_only


def test_more_deep_rem_and_duration_raise_score() -> None:
    poor = S.compute_sleep_score(sleep_hours=5.0, sleep_deep_hrs=0.3, sleep_rem_hrs=0.4)
    good = S.compute_sleep_score(
        sleep_hours=8.0,
        sleep_deep_hrs=8.0 * S.OPTIMAL_DEEP_FRACTION,
        sleep_rem_hrs=8.0 * S.OPTIMAL_REM_FRACTION,
    )
    assert poor is not None and good is not None
    assert good > poor


def test_better_hrv_raises_score_all_else_equal() -> None:
    low = S.compute_sleep_score(sleep_hours=8.0, hrv_rmssd=45.0, hrv_baseline=60.0)
    high = S.compute_sleep_score(sleep_hours=8.0, hrv_rmssd=72.0, hrv_baseline=60.0)
    assert low is not None and high is not None
    assert high > low


def test_lower_resting_hr_raises_score_all_else_equal() -> None:
    elevated = S.compute_sleep_score(sleep_hours=8.0, resting_hr=64.0, resting_hr_baseline=56.0)
    calm = S.compute_sleep_score(sleep_hours=8.0, resting_hr=50.0, resting_hr_baseline=56.0)
    assert elevated is not None and calm is not None
    assert calm > elevated


def test_recovery_components_ignored_without_baseline() -> None:
    """HRV / RHR are never guessed: identical scores whatever the raw value when no baseline."""
    a = S.compute_sleep_score(sleep_hours=7.0, hrv_rmssd=30.0, resting_hr=70.0)
    b = S.compute_sleep_score(sleep_hours=7.0, hrv_rmssd=90.0, resting_hr=45.0)
    duration_only = S.compute_sleep_score(sleep_hours=7.0)
    assert a == b == duration_only


def test_graceful_degradation_duration_only_still_scores() -> None:
    score = S.compute_sleep_score(sleep_hours=8.0)
    # Only the duration component is present; on-need sleep → full duration credit.
    assert score == pytest.approx(100.0)


def test_awake_interruptions_lower_score() -> None:
    restful = S.compute_sleep_score(sleep_hours=8.0, awake_hours=0.0)
    restless = S.compute_sleep_score(sleep_hours=8.0, awake_hours=1.2)
    assert restful is not None and restless is not None
    assert restful > restless


def test_score_clamped_to_0_100() -> None:
    # Wildly elevated HRV cannot push the score above 100.
    score = S.compute_sleep_score(
        sleep_hours=8.0, hrv_rmssd=500.0, hrv_baseline=50.0, resting_hr=30.0, resting_hr_baseline=60.0
    )
    assert score is not None and score <= 100.0


def test_trailing_baseline_mean_excludes_today_and_needs_two_samples() -> None:
    history = [
        {"metric_date": _d(1), "hrv_rmssd": 50.0},
        {"metric_date": _d(2), "hrv_rmssd": 60.0},
        {"metric_date": RUN, "hrv_rmssd": 999.0},  # today excluded
    ]
    assert S.trailing_baseline(history, metric="hrv_rmssd", as_of=RUN) == pytest.approx(55.0)
    # Only one prior sample → no baseline.
    assert S.trailing_baseline(history[:1], metric="hrv_rmssd", as_of=RUN) is None


def test_rollup_populates_native_sleep_score() -> None:
    rows = [
        {"metric": "sleep_hours", "value": 8.0},
        {"metric": "sleep_deep_hrs", "value": 1.5},
        {"metric": "sleep_rem_hrs", "value": 1.7},
        {"metric": "hrv_rmssd", "value": 62.0},
        {"metric": "resting_hr", "value": 54},
    ]
    wide = F.rollup_daily_health_metrics(
        rows,
        user_id="u1",
        metric_date=RUN,
        hrv_baseline=58.0,
        resting_hr_baseline=57.0,
    )
    assert "sleep_score" in wide
    assert 0.0 <= wide["sleep_score"] <= 100.0


def test_rollup_without_sleep_has_no_score() -> None:
    rows = [{"metric": "steps", "value": 8000}, {"metric": "resting_hr", "value": 55}]
    wide = F.rollup_daily_health_metrics(rows, user_id="u1", metric_date=RUN)
    assert "sleep_score" not in wide


def test_rollup_does_not_overwrite_existing_sleep_score() -> None:
    """If a source ever supplies sleep_score, the rollup keeps it rather than recomputing."""
    rows = [
        {"metric": "sleep_hours", "value": 8.0},
        {"metric": "sleep_score", "value": 42.0},
    ]
    wide = F.rollup_daily_health_metrics(rows, user_id="u1", metric_date=RUN)
    assert wide["sleep_score"] == pytest.approx(42.0)

"""Cardio plausibility tagging + its effect on features, rules, and mileage."""

from __future__ import annotations

from datetime import date

from pipeline import rules as rules_mod
from pipeline.cardio_quality import (
    FLAG_IMPLAUSIBLE_RUN_PACE,
    assess_cardio_quality,
    has_suspect_distance,
    is_overrecorded_distance,
)
from pipeline.features import compute_daily_features
from pipeline.mileage_ramp import iso_week_start, sum_running_km
from pipeline.units import KM_PER_MILE

# The real July 2 case: 35.8 min, 0.86 mi -> ~41:28 / mi (impossible for a run).
_BAD_RUN = {
    "event_date": "2026-07-02",
    "activity_type": "Outdoor Run",
    "duration_min": 35.8333,
    "distance_miles": 0.8642,
}
_GOOD_RUN = {
    "event_date": "2026-07-02",
    "activity_type": "Outdoor Run",
    "duration_min": 50.0,
    "distance_miles": 6.0,  # 8:20 / mi
}


def test_impossible_run_pace_is_flagged():
    assert assess_cardio_quality(_BAD_RUN) == [FLAG_IMPLAUSIBLE_RUN_PACE]
    assert has_suspect_distance(assess_cardio_quality(_BAD_RUN))


def test_plausible_run_is_clean():
    assert assess_cardio_quality(_GOOD_RUN) == []
    assert not has_suspect_distance([])


def test_impossibly_fast_run_is_flagged():
    fast = {"activity_type": "Run", "duration_min": 5.0, "distance_miles": 3.0}  # 1:40/mi
    assert assess_cardio_quality(fast) == [FLAG_IMPLAUSIBLE_RUN_PACE]


def test_non_run_and_missing_distance_never_flagged():
    walk = {"activity_type": "Outdoor Walk", "duration_min": 60.0, "distance_miles": 1.0}
    treadmill = {"activity_type": "Outdoor Run", "duration_min": 30.0, "distance_miles": None}
    assert assess_cardio_quality(walk) == []
    assert assess_cardio_quality(treadmill) == []


def test_ssm_band_override_changes_verdict():
    # Widen the ceiling past the bad run's pace -> no longer suspect.
    assert assess_cardio_quality(_BAD_RUN, run_pace_max_sec_mi=3000.0) == []


def test_features_count_suspect_runs_and_rules_flag():
    features = compute_daily_features(
        user_id="u1",
        feature_date=date(2026, 7, 3),
        cardio_events=[_BAD_RUN, _GOOD_RUN],
    )
    assert features["cardio_distance_suspect_7d"] == 1

    flags = rules_mod.evaluate(features=features)
    codes = {f.code for f in flags}
    assert "DATA_QUALITY_CARDIO_DISTANCE" in codes
    dq = next(f for f in flags if f.code == "DATA_QUALITY_CARDIO_DISTANCE")
    assert dq.severity == "info"


def test_mileage_keeps_slow_pace_run_but_still_flags_it():
    """A slow-pace run (paused timer / walk breaks) covers a real distance, so it
    counts toward weekly mileage — the July 2 "0 mi even though a run exists" bug.
    It is still surfaced as a data-quality note via assess_cardio_quality."""
    ws = iso_week_start(date(2026, 7, 3))
    km = sum_running_km(week_start=ws, running_sessions=[], cardio_events=[_BAD_RUN, _GOOD_RUN])
    # Both runs now contribute: the good 6 mi plus the slow run's real 0.8642 mi.
    assert km == round((6.0 + 0.8642) * KM_PER_MILE, 2)
    assert km > 0
    assert has_suspect_distance(assess_cardio_quality(_BAD_RUN))
    assert not is_overrecorded_distance(_BAD_RUN)


def test_mileage_excludes_overrecorded_fast_run():
    """An implausibly fast pace means the distance was over-recorded (GPS glitch);
    it is excluded so it cannot inflate mileage."""
    ws = iso_week_start(date(2026, 7, 3))
    fast = {
        "event_date": "2026-07-02",
        "activity_type": "Outdoor Run",
        "duration_min": 5.0,
        "distance_miles": 3.0,  # 1:40 / mi
    }
    assert is_overrecorded_distance(fast)
    km = sum_running_km(week_start=ws, running_sessions=[], cardio_events=[fast, _GOOD_RUN])
    assert km == round(6.0 * KM_PER_MILE, 2)

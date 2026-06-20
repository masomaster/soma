"""Tests for mileage ramp detection."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.mileage_ramp import check_mileage_ramp, iso_week_start, sum_running_km

RUN = date(2024, 6, 8)


def test_iso_week_start_is_monday():
    assert iso_week_start(date(2024, 6, 5)).weekday() == 0  # Wed -> Mon Jun 3


def test_mileage_spike_flagged():
    this_start = iso_week_start(RUN)
    last_start = this_start - timedelta(days=7)
    sessions = [
        {"session_date": this_start, "distance_km": 20},
        {"session_date": last_start, "distance_km": 10},
    ]
    result = check_mileage_ramp(run_date=RUN, running_sessions=sessions)
    assert result["flag"] == "mileage_spike"
    assert result["change_pct"] == 100.0


def test_sum_running_km_includes_cardio_miles():
    week = iso_week_start(RUN)
    km = sum_running_km(
        week_start=week,
        running_sessions=[],
        cardio_events=[{"event_date": week, "activity_type": "run", "distance_miles": 3.1}],
    )
    assert km > 4.9

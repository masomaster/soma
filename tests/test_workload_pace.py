"""Tests for rolling-window workload pace indicators and RYG status."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.strength_analytics import is_hard_set
from pipeline.workload_pace import (
    build_workload_pace_summary,
    calendar_week_cardio_load,
    calendar_week_strength_load_lbs,
    pace_status_message,
    window_cardio_load,
)


def _strength_row(*, event_date: date, reps: int, weight_lbs: float) -> dict:
    return {
        "event_date": event_date,
        "exercise_name": "Squat",
        "set_type": "working" if is_hard_set("working") else "warmup",
        "reps": reps,
        "weight_lbs": weight_lbs,
    }


def _cardio_row(
    *,
    event_date: date,
    activity_type: str,
    duration_min: float,
    distance_miles: float | None = None,
) -> dict:
    row = {
        "event_date": event_date,
        "activity_type": activity_type,
        "duration_min": duration_min,
    }
    if distance_miles is not None:
        row["distance_miles"] = distance_miles
    return row


def test_calendar_week_strength_load_sums_working_sets():
    week_start = date(2024, 6, 3)  # Monday
    events = [
        _strength_row(event_date=week_start, reps=5, weight_lbs=200),
        _strength_row(event_date=week_start + timedelta(days=1), reps=5, weight_lbs=100),
    ]
    assert calendar_week_strength_load_lbs(events, week_start=week_start) == 1500.0


def test_build_workload_pace_summary_red_on_strength_spike():
    as_of = date(2024, 6, 16)
    events: list[dict] = []
    # Four prior 7d windows at 10k lb each (ending 6/9, 6/2, 5/26, 5/19).
    for back in range(4, 0, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(_strength_row(event_date=end, reps=10, weight_lbs=1000))
    # Acute 7d doubles to 20k.
    events.append(_strength_row(event_date=as_of, reps=20, weight_lbs=1000))
    summary = build_workload_pace_summary(strength_events=events, cardio_events=[], as_of=as_of)
    lifting = summary["lifting"]
    assert lifting["status"] in ("yellow", "red")
    assert lifting["direction"] == "high"
    assert "Overloaded" in lifting["label"] or "ease up" in lifting["label"]
    assert lifting["wow_change_pct"] is not None
    assert lifting["wow_change_pct"] >= 50
    assert lifting["acute_window_end"] == as_of.isoformat()


def test_midweek_status_uses_rolling_seven_days_through_today():
    as_of = date(2024, 6, 15)  # Saturday
    events: list[dict] = []
    # Steady prior windows at 10k.
    for back in range(4, 0, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(_strength_row(event_date=end, reps=10, weight_lbs=1000))
    # Light acute window — underload, but still green (room to push).
    events.append(_strength_row(event_date=as_of, reps=1, weight_lbs=100))
    summary = build_workload_pace_summary(strength_events=events, cardio_events=[], as_of=as_of)
    lifting = summary["lifting"]
    assert lifting["acute_load"] == 100.0
    assert lifting["acute_window_start"] == (as_of - timedelta(days=6)).isoformat()
    assert lifting["acute_window_end"] == as_of.isoformat()
    assert lifting["status"] == "green"
    assert lifting["direction"] == "low"
    assert "Underloaded" in lifting["label"]
    assert "Overloaded" not in lifting["label"]


def test_underload_is_green_not_yellow():
    as_of = date(2024, 6, 16)
    events: list[dict] = []
    for back in range(4, 0, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(_strength_row(event_date=end, reps=20, weight_lbs=1000))
    events.append(_strength_row(event_date=as_of, reps=2, weight_lbs=1000))
    summary = build_workload_pace_summary(strength_events=events, cardio_events=[], as_of=as_of)
    lifting = summary["lifting"]
    assert lifting["acwr"] is not None and lifting["acwr"] < 0.6
    assert lifting["status"] == "green"
    assert lifting["direction"] == "low"
    assert "Underloaded" in lifting["label"]
    assert "Overloaded" not in lifting["label"]


def test_build_workload_pace_summary_cardio_acwr_green_zone():
    as_of = date(2024, 6, 16)
    events: list[dict] = []
    # One 5-mi run at the end of acute + each of the four prior 7d windows.
    for back in range(4, -1, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(
            _cardio_row(
                event_date=end,
                activity_type="Run",
                duration_min=60,
                distance_miles=5.0,
            )
        )
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=as_of)
    cardio = summary["cardio"]
    assert cardio["load_unit"] == "mi"
    assert cardio["status"] == "green"
    assert cardio["acwr"] is not None
    assert 0.8 <= cardio["acwr"] <= 1.3
    assert cardio["direction"] in (None, "low")
    assert cardio["acute_load"] == 5.0


def test_steady_cardio_not_red_from_stale_calendar_week():
    """Rolling lights should not flag a quiet recent 7d as overload from an old Mon–Sun week."""
    as_of = date(2024, 7, 11)  # Thursday mid-week
    events: list[dict] = []
    # Big completed calendar week far in the past (Jun 24–30) should not drive status.
    events.append(
        _cardio_row(
            event_date=date(2024, 6, 29),
            activity_type="Run",
            duration_min=200,
            distance_miles=20.0,
        )
    )
    # Steady recent rolling windows at ~5 mi each.
    for back in range(4, 0, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(
            _cardio_row(
                event_date=end, activity_type="Run", duration_min=60, distance_miles=5.0
            )
        )
    events.append(
        _cardio_row(event_date=as_of, activity_type="Run", duration_min=60, distance_miles=5.0)
    )
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=as_of)
    cardio = summary["cardio"]
    assert cardio["status"] == "green"
    assert cardio["acute_load"] == 5.0
    assert cardio["direction"] in (None, "low")


def test_cardio_light_ignores_non_run_minutes():
    """Long rides must not trip the cardio pace light — only running mileage counts."""
    as_of = date(2024, 6, 16)
    events: list[dict] = []
    for back in range(4, 0, -1):
        end = as_of - timedelta(days=7 * back)
        events.append(
            _cardio_row(
                event_date=end, activity_type="Run", duration_min=40, distance_miles=4.0
            )
        )
    # Huge bike volume in the acute window; running stays flat at 4 mi.
    events.append(
        _cardio_row(event_date=as_of, activity_type="Ride", duration_min=300, distance_miles=80.0)
    )
    events.append(
        _cardio_row(event_date=as_of, activity_type="Run", duration_min=40, distance_miles=4.0)
    )
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=as_of)
    cardio = summary["cardio"]
    assert cardio["acute_load"] == 4.0
    assert cardio["status"] == "green"
    assert summary["cardio_minutes"]["weekly_rollups"]


def test_strength_typed_cardio_excluded_from_cardio_minutes():
    week_start = date(2024, 6, 3)
    events = [
        _cardio_row(event_date=week_start, activity_type="Outdoor Run", duration_min=40),
        _cardio_row(
            event_date=week_start,
            activity_type="Traditional Strength Training",
            duration_min=50,
        ),
    ]
    assert calendar_week_cardio_load(events, week_start=week_start) == 40.0
    assert window_cardio_load(events, start=week_start, end=week_start + timedelta(days=6)) == 40.0


def test_running_and_cycling_weekly_rollups_present():
    anchor = date(2024, 6, 10)
    events = [
        _cardio_row(event_date=anchor, activity_type="Run", duration_min=40, distance_miles=4.0),
        _cardio_row(event_date=anchor, activity_type="Ride", duration_min=50, distance_miles=12.0),
    ]
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=anchor)
    assert summary["running"]["weekly_rollups"]
    assert summary["cycling"]["weekly_rollups"]
    assert summary["running"]["weekly_rollups"][-1]["load"] == 4.0
    assert summary["cycling"]["weekly_rollups"][-1]["load"] == 12.0


def test_return_from_rest_without_four_week_baseline_stays_unknown():
    """A first session after empty weeks is not a week-to-week overload."""
    as_of = date(2024, 7, 11)
    events = [
        _cardio_row(
            event_date=as_of, activity_type="Run", duration_min=60, distance_miles=5.0
        ),
    ]
    summary = build_workload_pace_summary(strength_events=[], cardio_events=events, as_of=as_of)
    cardio = summary["cardio"]
    assert cardio["acute_load"] == 5.0
    assert cardio["four_week_avg_load"] is None
    assert cardio["wow_change_pct"] is None
    assert cardio["status"] == "unknown"
    assert "Building baseline" in cardio["label"]


def test_week_over_week_spike_does_not_set_the_light():
    """A jump versus last week stays green when it matches the 4-week average."""
    as_of = date(2024, 6, 16)
    events: list[dict] = []
    # Prior four windows and the acute window are 10k. The week before acute
    # is also one of those windows, so WoW is flat. Add a quiet week just
    # before acute by putting the previous window's load on a day that is NOT
    # in the prior-7d window... simpler: make prior 7d small and the other
    # three chronic weeks large enough that acute matches the 4-week mean.
    # Windows ending as_of-28, -21, -14 are 20k; window ending as_of-7 is 2k;
    # acute is 15.5k. Mean of prior four = (20+20+20+2)/4 = 15.5, ACWR = 1.0.
    # WoW = (15.5-2)/2 = +675%.
    for back, reps in ((4, 20), (3, 20), (2, 20), (1, 2)):
        end = as_of - timedelta(days=7 * back)
        events.append(_strength_row(event_date=end, reps=reps, weight_lbs=1000))
    events.append(_strength_row(event_date=as_of, reps=15, weight_lbs=1000))
    # 15*1000 = 15000; chronic = (20000+20000+20000+2000)/4 = 15500; ACWR ~ 0.97.
    summary = build_workload_pace_summary(strength_events=events, cardio_events=[], as_of=as_of)
    lifting = summary["lifting"]
    assert lifting["wow_change_pct"] is not None and lifting["wow_change_pct"] > 100
    assert lifting["status"] == "green"
    assert lifting["direction"] in (None, "low")


def test_pace_status_message_uses_four_week_average():
    domain = {
        "status": "yellow",
        "emoji": "🟡",
        "label": "Cautious — ease up a little",
        "load_unit": "mi",
        "acute_load": 18,
        "four_week_avg_load": 14,
        "acwr": 1.35,
        "wow_change_pct": 12.0,
        "vs_monthly_avg_pct": 28.6,
    }
    msg = pace_status_message(domain)
    assert "🟡" in msg
    assert "vs 4-wk avg" in msg
    assert "WoW" not in msg
    # Change over four weeks leads the numeric summary.
    assert msg.index("+28.6% vs 4-wk avg") < msg.index("18 mi last 7d")

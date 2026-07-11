"""Tests for the deterministic 'At a Glance' briefing summary (pure, no IO)."""

from __future__ import annotations

from datetime import date

from pipeline import metrics_summary as M
from pipeline.rules import Flag

RUN = date(2024, 6, 8)  # Saturday — ISO week Mon 2024-06-03 … Sun 2024-06-09


def test_build_glance_metrics_orders_and_formats_available_values():
    week_activity = {
        "run_sessions": 3,
        "strength_sessions": 1,
        "strength_hard_sets": 10,
        "strength_tonnage_short_tons": 3.25,
        "strength_volume_lbs": 6500.0,
        "cardio_sessions": 2,
        "cardio_minutes": 60.0,
    }
    metrics = M.build_glance_metrics(
        features={"overall_readiness_score": 88.0},
        daily_metrics={"resting_hr": 50, "hrv_rmssd": 61.0, "sleep_hours": 8.0, "body_weight_lbs": 176.4},
        flags=[],
        goal_snapshot={"mileage_check": {"this_week_km": 18.4}},
        week_activity=week_activity,
    )
    labels = [label for label, _ in metrics]
    assert labels == [
        "Runs (this week)",
        "Strength (this week)",
        "Cardio (this week)",
        "Lifting tonnage (this week)",
        "Run distance (this week)",
        "Resting HR",
        "HRV (last night)",
        "Sleep (last night)",
        "Body weight",
        "Readiness",
        "Red flags",
    ]
    values = dict(metrics)
    assert values["Strength (this week)"] == "1 session · 10 hard sets"
    assert values["Cardio (this week)"] == "2 sessions · 60 min"
    assert values["Lifting tonnage (this week)"] == "3.2 short tons (6,500 lb)"
    assert values["Run distance (this week)"] == "11.4 mi"
    assert values["Resting HR"] == "50 bpm"
    assert values["Readiness"] == "88/100"
    assert values["Red flags"] == "None"
    assert "Key lifts" not in labels


def test_build_glance_metrics_omits_key_lifts_even_when_strength_progress_has_them():
    metrics = M.build_glance_metrics(
        features={},
        week_activity={
            "run_sessions": 0,
            "strength_sessions": 1,
            "strength_hard_sets": 5,
            "strength_tonnage_short_tons": 1.0,
            "strength_volume_lbs": 2000.0,
            "cardio_sessions": 0,
            "cardio_minutes": 0.0,
        },
        strength_progress={
            "this_week_volume_lbs": 2000.0,
            "week_over_week_change_pct": 10.0,
            "top_exercises": [
                {"exercise_name": "Bench", "latest_top_weight_lbs": 185.0},
            ],
        },
    )
    labels = [label for label, _ in metrics]
    assert "Key lifts (latest)" not in labels
    assert "Lifting vs last week" in labels


def test_build_glance_metrics_omits_missing_but_always_reports_red_flags():
    metrics = M.build_glance_metrics(features={}, daily_metrics=None, flags=[])
    assert metrics == [("Red flags", "None")]


def test_red_flag_line_lists_only_warning_and_alert():
    flags = [
        Flag(code="LOW_HRV", severity="alert", message="x"),
        Flag(code="LOW_SLEEP", severity="warning", message="y"),
        Flag(code="SPARSE_RECOVERY_DATA", severity="info", message="z"),
    ]
    metrics = dict(M.build_glance_metrics(features={}, flags=flags))
    assert metrics["Red flags"] == "2 — LOW_HRV, LOW_SLEEP"


def test_render_glance_block_is_heading_plus_bullets():
    block = M.render_glance_block([("Runs (this week)", "3"), ("Red flags", "None")])
    assert block == (
        "## At a Glance\n\n- **Runs (this week):** 3\n- **Red flags:** None"
    )


def test_render_glance_block_empty_for_no_metrics():
    assert M.render_glance_block([]) == ""


def test_count_run_sessions_this_week_counts_distinct_days_in_calendar_week():
    # RUN is Sat 2024-06-08; week is Mon 6/3 – Sun 6/9
    cardio = [
        {"event_date": RUN, "activity_type": "Outdoor Run"},
        {"event_date": RUN, "activity_type": "trail run"},  # same day, still 1
        {"event_date": RUN, "activity_type": "Cycling"},  # not a run
        {"event_date": date(2024, 5, 27), "activity_type": "run"},  # prior week
        {"event_date": date(2024, 6, 3), "activity_type": "run"},  # Mon this week
        {"event_date": RUN, "activity_type": "Traditional Strength Training"},
    ]
    running = [{"session_date": date(2024, 6, 7), "run_type": "easy"}]
    assert M.count_run_sessions_this_week(cardio, running, as_of=RUN) == 3


def test_calendar_week_glance_activity_excludes_strength_typed_cardio():
    cardio = [
        {"event_date": RUN, "activity_type": "Outdoor Run", "duration_min": 30},
        {
            "event_date": RUN,
            "activity_type": "Traditional Strength Training",
            "duration_min": 45,
        },
    ]
    strength = [
        {
            "event_date": RUN,
            "set_type": "working",
            "reps": 5,
            "weight_lbs": 200,
        }
    ]
    week = M.calendar_week_glance_activity(
        as_of=RUN, strength_events=strength, cardio_events=cardio
    )
    assert week["run_sessions"] == 1
    assert week["cardio_minutes"] == 30.0
    assert week["cardio_sessions"] == 1
    assert week["strength_volume_lbs"] == 1000.0


def test_format_glance_section_renders_full_markdown():
    section = M.format_glance_section(
        features={"overall_readiness_score": 90.0},
        daily_metrics={"resting_hr": 48},
        flags=[Flag(code="LOW_SLEEP", severity="warning", message="x")],
    )
    assert section == (
        "## At a Glance\n\n"
        "- **Resting HR:** 48 bpm\n"
        "- **Readiness:** 90/100\n"
        "- **Red flags:** 1 — LOW_SLEEP"
    )

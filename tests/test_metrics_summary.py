"""Tests for the deterministic 'At a Glance' briefing summary (pure, no IO)."""

from __future__ import annotations

from datetime import date

from pipeline import metrics_summary as M
from pipeline.rules import Flag

RUN = date(2024, 6, 8)  # Saturday — ISO week Mon 2024-06-03 … Sun 2024-06-09


def test_build_glance_metrics_leads_with_weight_and_skips_recovery():
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
    assert labels == ["Body weight", "Weight vs 4-week avg"]
    values = dict(metrics)
    assert values["Body weight"] == "176.4 lb"
    assert values["Weight vs 4-week avg"] == (
        "Not enough weigh-ins in the 4 weeks before the last reading"
    )
    assert "Resting HR" not in labels
    assert "Readiness" not in labels
    assert "Red flags" not in labels
    assert "Runs (this week)" not in labels


def test_build_glance_metrics_omits_key_lifts_and_week_over_week_volume():
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
    assert "Lifting vs last week" not in labels
    assert labels == ["Body weight"]


def test_build_glance_metrics_reports_missing_weigh_in():
    metrics = M.build_glance_metrics(features={}, daily_metrics=None, flags=[])
    assert metrics == [("Body weight", "No weigh-in logged")]


def test_glance_does_not_list_recovery_flags():
    flags = [
        Flag(code="LOW_HRV", severity="alert", message="x"),
        Flag(code="LOW_SLEEP", severity="warning", message="y"),
        Flag(code="SPARSE_RECOVERY_DATA", severity="info", message="z"),
    ]
    labels = [label for label, _ in M.build_glance_metrics(features={}, flags=flags)]
    assert "Red flags" not in labels


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


def test_build_glance_metrics_shows_load_against_four_week_average():
    metrics = dict(
        M.build_glance_metrics(
            features={},
            weight_trend={
                "latest_lbs": 165.0,
                "readings_7d": 3,
                "avg_7d_lbs": 165.2,
                "readings_28d": 8,
                "avg_28d_lbs": 166.0,
                "vs_4wk_lbs": -0.8,
            },
            workload_pace={
                "cardio": {
                    "acute_load": 180,
                    "four_week_avg_load": 140,
                    "vs_monthly_avg_pct": 28.6,
                    "load_unit": "min",
                    "status": "yellow",
                    "label": "Cautious — ease up a little",
                },
                "lifting": {
                    "acute_load": 4000,
                    "four_week_avg_load": 4200,
                    "vs_monthly_avg_pct": -4.8,
                    "load_unit": "lb",
                    "status": "green",
                    "label": "Good to go",
                },
            },
        )
    )
    assert metrics["Body weight"] == "165.0 lb · 7-day avg 165.2 lb"
    assert "−0.8 lb" in metrics["Weight vs 4-week avg"] or "-0.8 lb" in metrics["Weight vs 4-week avg"]
    assert metrics["Cardio load"].startswith("180 min last 7d")
    assert "4-wk avg 140 min" in metrics["Cardio load"]
    assert "Cautious" in metrics["Cardio load"]
    assert "Good to go" not in metrics["Lifting load"]
    assert "4-wk avg 4200 lb" in metrics["Lifting load"]


def test_format_glance_section_renders_weight_only_when_that_is_all_we_have():
    section = M.format_glance_section(
        features={"overall_readiness_score": 90.0},
        daily_metrics={"resting_hr": 48, "body_weight_lbs": 170.2},
        flags=[Flag(code="LOW_SLEEP", severity="warning", message="x")],
    )
    assert section == (
        "## At a Glance\n\n"
        "- **Body weight:** 170.2 lb\n"
        "- **Weight vs 4-week avg:** Not enough weigh-ins in the 4 weeks before the last reading"
    )

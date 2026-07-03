"""Tests for the deterministic 'At a Glance' briefing summary (pure, no IO)."""

from __future__ import annotations

from datetime import date

from pipeline import metrics_summary as M
from pipeline.rules import Flag

RUN = date(2024, 6, 8)


def test_build_glance_metrics_orders_and_formats_available_values():
    metrics = M.build_glance_metrics(
        features={
            "strength_sessions_7d": 1,
            "strength_hard_sets_7d": 10,
            "strength_tonnage_7d": 3.25,
            "cardio_sessions_7d": 2,
            "cardio_minutes_7d": 60.0,
            "overall_readiness_score": 88.0,
        },
        daily_metrics={"resting_hr": 50, "hrv_rmssd": 61.0, "sleep_hours": 8.0, "body_weight_lbs": 176.4},
        flags=[],
        goal_snapshot={"mileage_check": {"this_week_km": 18.4}},
        run_sessions_7d=3,
    )
    labels = [label for label, _ in metrics]
    assert labels == [
        "Runs (7d)",
        "Strength (7d)",
        "Cardio (7d)",
        "Lifting tonnage (7d)",
        "Run distance (this week)",
        "Resting HR",
        "HRV (last night)",
        "Sleep (last night)",
        "Body weight",
        "Readiness",
        "Red flags",
    ]
    values = dict(metrics)
    # Singular "session" when count is 1; whole numbers drop the decimal point.
    assert values["Strength (7d)"] == "1 session · 10 hard sets"
    assert values["Cardio (7d)"] == "2 sessions · 60 min"
    assert values["Lifting tonnage (7d)"] == "3.2 short tons (6,500 lb)"
    assert values["Run distance (this week)"] == "18.4 km"
    assert values["Resting HR"] == "50 bpm"
    assert values["Readiness"] == "88/100"
    assert values["Red flags"] == "None"


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
    block = M.render_glance_block([("Runs (7d)", "3"), ("Red flags", "None")])
    assert block == (
        "## At a Glance\n\n- **Runs (7d):** 3\n- **Red flags:** None"
    )


def test_render_glance_block_empty_for_no_metrics():
    assert M.render_glance_block([]) == ""


def test_count_run_sessions_7d_counts_distinct_days_in_window():
    cardio = [
        {"event_date": RUN, "activity_type": "Outdoor Run"},
        {"event_date": RUN, "activity_type": "trail run"},  # same day, still 1
        {"event_date": RUN, "activity_type": "Cycling"},  # not a run
        {"event_date": date(2024, 5, 1), "activity_type": "run"},  # outside 7d
    ]
    running = [{"session_date": RUN.replace(day=7), "run_type": "easy"}]
    assert M.count_run_sessions_7d(cardio, running, as_of=RUN) == 2


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

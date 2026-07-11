"""Phase 6 briefing-synthesis tests (injected LLM, no network)."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline import briefing as B
from pipeline.rules import Flag

RUN = date(2024, 6, 8)


def test_build_prompt_includes_flags_and_features():
    flags = [Flag(code="LOW_HRV", severity="alert", message="HRV suppressed.")]
    prompt = B.build_prompt(
        feature_date=RUN,
        flags=flags,
        features={"overall_readiness_score": 44.0, "missing": None},
        daily_metrics={"sleep_hours": 5.0},
    )
    assert "LOW_HRV" in prompt and "HRV suppressed." in prompt
    assert "overall_readiness_score" in prompt
    assert "missing" not in prompt  # None values are dropped
    assert "2024-06-08" in prompt
    assert "US short tons" in prompt
    assert "acute_chronic_ratio is the rolling" in prompt
    assert "recovery_sleep_days_7d is 0" in prompt
    assert "training_load_*" in prompt
    assert "effort_unified_index_*" in prompt
    assert "STATISTICAL_SIGNALS" in prompt
    assert "anomalies" in prompt


def test_build_prompt_handles_no_flags():
    prompt = B.build_prompt(feature_date=RUN, flags=[], features={})
    assert "None. All monitored signals" in prompt
    assert "STATISTICAL_SIGNALS" in prompt


def test_build_prompt_includes_stat_signals_block():
    stats = {"anomalies": [{"metric": "hrv_rmssd", "z_score": -2.5}], "trends": []}
    prompt = B.build_prompt(feature_date=RUN, flags=[], features={}, stat_signals=stats)
    assert '"metric": "hrv_rmssd"' in prompt
    assert "-2.5" in prompt


def test_build_prompt_includes_goal_snapshot():
    snap = {
        "goals_status": {"strength": {"completed": 1, "target": "3-4x", "status": "behind"}},
        "mileage_check": {"this_week_km": 5.0},
        "todays_focus": "Strength session needed",
    }
    prompt = B.build_prompt(feature_date=RUN, flags=[], features={}, goal_snapshot=snap)
    assert "GOALS_STATUS" in prompt
    assert "TODAYS_FOCUS" in prompt
    assert "Strength session needed" in prompt


def test_build_prompt_includes_guidelines():
    from pipeline.guidelines import GuidelinesContext

    ctx = GuidelinesContext(
        my_goals="Run a marathon in fall.",
        injury_history="Right knee — limit downhill volume.",
    )
    prompt = B.build_prompt(
        feature_date=RUN,
        flags=[],
        features={},
        guidelines=ctx,
    )
    assert "PERSONAL GOALS" in prompt
    assert "marathon" in prompt
    assert "INJURY HISTORY" in prompt
    assert "knee" in prompt


def test_generate_briefing_uses_llm_and_maps_to_row():
    captured = {}

    def fake_llm(system: str, user: str) -> str:
        captured["system"] = system
        captured["user"] = user
        return "  Take it easy today; HRV is down.  "

    flags = [Flag(code="LOW_HRV", severity="alert", message="HRV suppressed.")]
    briefing = B.generate_briefing(
        user_id="u1",
        feature_date=RUN,
        flags=flags,
        features={"overall_readiness_score": 44.0},
        llm=fake_llm,
    )
    assert briefing.coaching_note == (
        "# Morning Check-In · Saturday, June 8\n\n"
        "## At a Glance\n\n"
        "- **Readiness:** 44/100\n"
        "- **Red flags:** 1 — LOW_HRV\n\n"
        "Take it easy today; HRV is down."
    )
    assert briefing.flags == ["LOW_HRV"]
    assert briefing.model_used == B.DEFAULT_BRIEFING_MODEL
    assert captured["system"] == B.SYSTEM_GUIDELINES

    row = briefing.to_row()
    assert row["user_id"] == "u1"
    assert row["briefing_date"] == RUN
    assert row["flags"] == ["LOW_HRV"]
    assert row["coaching_note"].startswith("# Morning Check-In · Saturday, June 8")
    assert row["features_json"]["overall_readiness_score"] == 44.0
    assert row["features_json"]["stat_signals"]["anomalies"] == []
    assert row["features_json"]["stat_signals"]["trends"] == []


def test_generate_briefing_leads_with_glance_summary():
    flags = [
        Flag(code="HIGH_TRAINING_LOAD", severity="alert", message="ACWR high."),
        Flag(code="SPARSE_RECOVERY_DATA", severity="info", message="No recovery data."),
    ]
    week_activity = {
        "run_sessions": 4,
        "strength_sessions": 3,
        "strength_hard_sets": 42,
        "strength_tonnage_short_tons": 12.5,
        "strength_volume_lbs": 25000.0,
        "cardio_sessions": 2,
        "cardio_minutes": 95.0,
    }
    briefing = B.generate_briefing(
        user_id="u1",
        feature_date=RUN,
        flags=flags,
        features={"overall_readiness_score": 71.0},
        daily_metrics={"resting_hr": 52, "hrv_rmssd": 48.0, "sleep_hours": 7.5},
        week_activity=week_activity,
        llm=lambda system, user: (
            "- Ease cardio today.\n- Keep lifting volume steady.\n- Prioritize sleep tonight."
        ),
    )
    note = briefing.coaching_note
    glance_idx = note.index("## At a Glance")
    prose_idx = note.index("Ease cardio")
    assert note.index("# Morning Check-In") < glance_idx < prose_idx
    assert "- **Runs (this week):** 4" in note
    assert "- **Strength (this week):** 3 sessions · 42 hard sets" in note
    assert "- **Cardio (this week):** 2 sessions · 95 min" in note
    assert "- **Lifting tonnage (this week):** 12.5 short tons (25,000 lb)" in note
    assert "- **Resting HR:** 52 bpm" in note
    assert "- **HRV (last night):** 48 ms" in note
    assert "- **Sleep (last night):** 7.5 h" in note
    assert "- **Readiness:** 71/100" in note
    assert "Key lifts" not in note
    assert "- **Red flags:** 1 — HIGH_TRAINING_LOAD" in note
    assert "action bullets" in B.SYSTEM_GUIDELINES or "bullet points" in B.SYSTEM_GUIDELINES


def test_generate_briefing_rejects_empty_note():
    with pytest.raises(ValueError, match="empty"):
        B.generate_briefing(
            user_id="u1",
            feature_date=RUN,
            flags=[],
            features={},
            llm=lambda system, user: "   ",
        )


def test_format_briefing_title_uses_weekday_and_month():
    assert B.format_briefing_title(date(2026, 7, 2)) == "Morning Check-In · Thursday, July 2"
    assert B.format_briefing_title(RUN) == "Morning Check-In · Saturday, June 8"


def test_generate_briefing_replaces_llm_supplied_title():
    briefing = B.generate_briefing(
        user_id="u1",
        feature_date=date(2026, 7, 2),
        flags=[],
        features={},
        llm=lambda system, user: "# Daily Update\n\nSolid recovery overnight.",
    )
    # The model's own heading is dropped; the canonical title is enforced once.
    assert briefing.coaching_note == (
        "# Morning Check-In · Thursday, July 2\n\n"
        "## At a Glance\n\n"
        "- **Red flags:** None\n\n"
        "Solid recovery overnight."
    )
    assert briefing.coaching_note.count("Morning Check-In") == 1


def test_generate_briefing_strips_trailing_question():
    briefing = B.generate_briefing(
        user_id="u1",
        feature_date=date(2026, 7, 2),
        flags=[],
        features={},
        llm=lambda system, user: "Recovery looks solid. How are you feeling?",
    )
    assert briefing.coaching_note == (
        "# Morning Check-In · Thursday, July 2\n\n"
        "## At a Glance\n\n"
        "- **Red flags:** None\n\n"
        "Recovery looks solid."
    )
    assert "?" not in briefing.coaching_note


def test_system_guidelines_forbid_titles_and_questions():
    guidelines = B.SYSTEM_GUIDELINES.lower()
    assert "no closing question" in guidelines or "not end with a question" in guidelines
    assert "morning check-in" in guidelines
    assert "bullet" in guidelines

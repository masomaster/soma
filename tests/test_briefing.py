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


def test_build_prompt_handles_no_flags():
    prompt = B.build_prompt(feature_date=RUN, flags=[], features={})
    assert "None. All monitored signals" in prompt


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
    assert briefing.coaching_note == "Take it easy today; HRV is down."
    assert briefing.flags == ["LOW_HRV"]
    assert briefing.model_used == B.DEFAULT_BRIEFING_MODEL
    assert captured["system"] == B.SYSTEM_GUIDELINES

    row = briefing.to_row()
    assert row["user_id"] == "u1"
    assert row["briefing_date"] == RUN
    assert row["flags"] == ["LOW_HRV"]
    assert row["coaching_note"].startswith("Take it easy")
    assert row["features_json"] == {"overall_readiness_score": 44.0}


def test_generate_briefing_rejects_empty_note():
    with pytest.raises(ValueError, match="empty"):
        B.generate_briefing(
            user_id="u1",
            feature_date=RUN,
            flags=[],
            features={},
            llm=lambda system, user: "   ",
        )

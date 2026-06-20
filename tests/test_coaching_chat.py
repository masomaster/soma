"""Tests for Slice C coaching chat."""

from __future__ import annotations

from pipeline.coaching_chat import extract_tool_calls, run_coaching_turn


def test_extract_tool_calls():
    text = 'Sure.\n{"tool_calls": [{"name": "log_run", "arguments": {"run_type": "easy"}}]}'
    calls = extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "log_run"


def test_run_coaching_turn_mock_llm():
    ctx = {"user_id": "u1", "todays_focus": "Rest day"}
    turn = run_coaching_turn(
        user_id="u1",
        user_message="How am I doing?",
        dashboard_context=ctx,
        messages=[],
        llm=lambda s, p: "You're on track for recovery.",
    )
    assert "track" in turn["reply"].lower()

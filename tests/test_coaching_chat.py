"""Tests for Slice C coaching chat."""

from __future__ import annotations

from pipeline.coaching_chat import (
    CHAT_SYSTEM,
    extract_tool_calls,
    format_chat_prompt,
    run_coaching_turn,
)


def test_chat_system_has_correlation_guidance():
    lower = CHAT_SYSTEM.lower()
    assert "correlat" in lower
    # explicitly instructs the model NOT to refuse correlation questions
    assert "do not refuse" in lower
    assert "correlations" in lower  # points at the DASHBOARD_CONTEXT block


def test_chat_prompt_includes_precomputed_correlations():
    prompt = format_chat_prompt(
        dashboard_context={
            "user_id": "u1",
            "correlations": [
                {
                    "metric_a": "sleep_hours",
                    "metric_b": "strength_tonnage_7d",
                    "lag_days": 1,
                    "correlation": 0.72,
                    "direction": "positive",
                    "sample_n": 21,
                    "summary": "sleep hours vs 7d strength tonnage (lag 1d)",
                }
            ],
        },
        messages=[],
        user_message="how does my sleep correlate with my lifting gains?",
    )
    assert "correlations" in prompt
    assert "strength_tonnage_7d" in prompt


def test_extract_tool_calls():
    text = 'Sure.\n{"tool_calls": [{"name": "log_run", "arguments": {"run_type": "easy"}}]}'
    calls = extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "log_run"


def test_run_coaching_turn_includes_guidelines_in_prompt():
    from pipeline.coaching_chat import format_chat_prompt
    from pipeline.guidelines import GuidelinesContext

    prompt = format_chat_prompt(
        dashboard_context={"user_id": "u1"},
        messages=[],
        user_message="Hello",
        guidelines=GuidelinesContext(injury_history="Avoid overhead press."),
    )
    assert "INJURY HISTORY" in prompt
    assert "overhead" in prompt.lower()


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


UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _history_llm(system: str, prompt: str) -> str:
    """Dispatch the three LLM calls of a query_history turn by their system prompt."""
    if "PostgreSQL SELECT" in system:  # generate_bounded_sql
        return (
            "SELECT metric_date, sleep_hours FROM daily_health_metrics "
            f"WHERE user_id = '{UID}' LIMIT 30"
        )
    if "query results" in system:  # summarize_query_result
        return "Your sleep averaged 8.2 h over the last 30 days."
    return '{"tool_calls": [{"name": "query_history", "arguments": {"question": "sleep 30d?"}}]}'


def test_query_history_tool_runs_and_summarizes() -> None:
    rows = [{"metric_date": "2026-07-02", "sleep_hours": 7.4}]
    turn = run_coaching_turn(
        user_id=UID,
        user_message="How has my sleep trended over 30 days?",
        dashboard_context={"user_id": UID},
        messages=[],
        llm=_history_llm,
        query_all=lambda sql, params: rows,
    )
    assert "8.2" in turn["reply"]
    assert turn["pending_writes"] == []
    assert len(turn["query_results"]) == 1
    assert turn["query_results"][0]["ok"] is True
    assert turn["query_results"][0]["row_count"] == 1


def test_query_history_tool_noop_without_executor() -> None:
    turn = run_coaching_turn(
        user_id=UID,
        user_message="sleep trend?",
        dashboard_context={"user_id": UID},
        messages=[],
        llm=_history_llm,
        query_all=None,
    )
    assert turn["query_results"] == []
    assert turn["pending_writes"] == []

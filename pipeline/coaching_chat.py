"""Multi-turn coaching chat (Slice C).

Uses the same bounded JSON context as the daily briefing plus recent
messages. Write paths go through :mod:`pipeline.goal_tools` only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pipeline.briefing import LLMClient, SYSTEM_GUIDELINES
from pipeline.goal_tools import COACHING_TOOL_SCHEMAS, apply_tool_call

CHAT_SYSTEM = (
    f"{SYSTEM_GUIDELINES}\n\n"
    "You are also in a coaching chat. Answer using the DASHBOARD_CONTEXT JSON. "
    "For goal or run changes, respond with a JSON block on its own line: "
    '{"tool_calls": [{"name": "...", "arguments": {...}}]}. '
    "Use tools from the fixed list only. Confirm material changes briefly."
)


def format_chat_prompt(
    *,
    dashboard_context: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    user_message: str,
) -> str:
    """Build the user prompt for one chat turn."""
    history_lines: list[str] = []
    for msg in messages[-12:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history_lines.append(f"{role.upper()}: {content}")
    ctx_blob = json.dumps(dashboard_context, indent=2, sort_keys=True, default=str)
    tools_blob = json.dumps(COACHING_TOOL_SCHEMAS, indent=2)
    history = "\n".join(history_lines) if history_lines else "(no prior messages)"
    return (
        f"DASHBOARD_CONTEXT:\n{ctx_blob}\n\n"
        f"AVAILABLE_TOOLS:\n{tools_blob}\n\n"
        f"CHAT_HISTORY:\n{history}\n\n"
        f"USER: {user_message.strip()}\n\n"
        "Reply conversationally. If a tool call is needed, include the JSON block."
    )


def extract_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse optional tool_calls JSON from assistant text."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
            return [tc for tc in payload["tool_calls"] if isinstance(tc, dict)]
    return []


def run_coaching_turn(
    *,
    user_id: str,
    user_message: str,
    dashboard_context: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    llm: LLMClient,
) -> dict[str, Any]:
    """One chat turn: LLM reply + optional validated tool invocations."""
    prompt = format_chat_prompt(
        dashboard_context=dashboard_context,
        messages=messages,
        user_message=user_message,
    )
    reply = llm(CHAT_SYSTEM, prompt).strip()
    tool_calls_raw = extract_tool_calls(reply)
    tool_results: list[dict[str, Any]] = []
    pending_writes: list[dict[str, Any]] = []
    for tc in tool_calls_raw:
        name = tc.get("name")
        args = tc.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        try:
            result = apply_tool_call(name, args, user_id=user_id)
            tool_results.append({"tool": name, "ok": True, "result": result})
            pending_writes.append(result)
        except (ValueError, TypeError) as exc:
            tool_results.append({"tool": name, "ok": False, "error": str(exc)})
    # Strip JSON tool block from user-visible reply
    visible = "\n".join(
        ln for ln in reply.splitlines() if not ln.strip().startswith('{"tool_calls"')
    ).strip()
    return {
        "reply": visible or reply,
        "tool_results": tool_results,
        "pending_writes": pending_writes,
    }

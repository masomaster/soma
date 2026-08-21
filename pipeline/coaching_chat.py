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
from pipeline.guidelines import GuidelinesContext, format_guidelines_for_prompt
from pipeline.history_query import QueryAll, run_history_query, summarize_query_result

QUERY_HISTORY_TOOL = "query_history"

CHAT_SYSTEM = (
    f"{SYSTEM_GUIDELINES}\n\n"
    "You are also in a coaching chat. Answer using the DASHBOARD_CONTEXT JSON and any "
    "PERSONAL GOALS / INJURY HISTORY blocks above it. Respect injury constraints. "
    "DASHBOARD_CONTEXT.athlete_journal lists the athlete's saved notes (workout feel, "
    "supplements, recovery) — cite them when relevant; do not invent journal entries. "
    "DASHBOARD_CONTEXT.training_phase lists scheduled blocks (active, upcoming, all_phases "
    "with ids). When the athlete asks to schedule, change, pause, table, cancel, or replace "
    "a training block, you MUST emit tool_calls — never claim you updated a phase in prose "
    "alone. Prefer deactivate_training_phase (or update_training_phase end_date) on the "
    "current overlapping phase id from all_phases, then set_training_phase for the new "
    "block(s). Bulk/hypertrophy maps to phase_type building; cardio/run focus maps to "
    "running. When switching plans, deactivate or end the old block so phases do not "
    "overlap. "
    "When the athlete shares something you should remember later (e.g. a hard chest day, "
    "starting creatine, feeling run-down), call log_journal_entry with an appropriate "
    "category instead of only replying in prose — the entry is persisted and appears in "
    "future briefings and chat context. "
    "DASHBOARD_CONTEXT only holds the latest snapshot; when the athlete asks about "
    "history or trends over time (sleep, HRV, resting HR, weight, cardio, strength), "
    f"call the {QUERY_HISTORY_TOOL!r} tool with a natural-language 'question' instead "
    "of guessing or claiming you lack the data — the results are fetched and summarized "
    "for you. "
    "When the athlete asks how two metrics RELATE or CORRELATE (e.g. 'how does my sleep "
    "correlate with my cardio and lifting gains?'), do NOT refuse and do NOT try to "
    "compute your own statistics: the pipeline pre-computes these correlations for you. "
    "Cite the entries in DASHBOARD_CONTEXT.correlations — each has metric_a, metric_b, "
    "lag_days, correlation (Pearson r), direction, and sample_n. Report the r value, "
    "direction (positive/negative), the lag in days, and the sample size, then give a "
    "plain-English interpretation (e.g. 'more sleep tends to precede higher 7-day cardio "
    "volume'). Never invent a correlation that is not listed. If "
    "DASHBOARD_CONTEXT.correlations is absent or empty, say honestly that no significant "
    "correlation has been confirmed yet and that more logged days are needed (the scan "
    f"needs at least ~2 weeks of overlapping data) — you may also call {QUERY_HISTORY_TOOL!r} "
    "against the metric_patterns table to double-check. Never claim you 'can only fetch one "
    "metric at a time'. "
    "All distances are in statute MILES (mi) — report running and cycling distance in "
    "miles, never km, and when logging a run pass distance_miles. "
    "For goal, run, journal, or training-phase changes, include a JSON object that "
    'contains "tool_calls": [{"name": "...", "arguments": {...}}] (own line preferred; '
    "markdown fences OK). Use tools from the fixed list only. Confirm material changes "
    "briefly after the tools."
)


def format_chat_prompt(
    *,
    dashboard_context: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    user_message: str,
    guidelines: GuidelinesContext | None = None,
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
    guidelines_block = format_guidelines_for_prompt(guidelines)
    return (
        f"{guidelines_block}"
        f"DASHBOARD_CONTEXT:\n{ctx_blob}\n\n"
        f"AVAILABLE_TOOLS:\n{tools_blob}\n\n"
        f"CHAT_HISTORY:\n{history}\n\n"
        f"USER: {user_message.strip()}\n\n"
        "Reply conversationally. If a tool call is needed, include the JSON block."
    )


def _tool_calls_from_payload(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
        return [tc for tc in payload["tool_calls"] if isinstance(tc, dict)]
    return None


def _extract_balanced_json_object(text: str, start: int) -> str | None:
    """Return the substring of the first balanced ``{...}`` starting at ``start``."""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def extract_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse optional tool_calls JSON from assistant text.

    Accepts a bare JSON line, a fenced markdown ``json`` code block, or a
    ``tool_calls`` object embedded in surrounding prose — models often wrap or
    nest the block.
    """
    # 1) Whole-line JSON (historical path).
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("```"):
            continue
        if not line.startswith("{"):
            continue
        try:
            found = _tool_calls_from_payload(json.loads(line))
        except json.JSONDecodeError:
            continue
        if found is not None:
            return found

    # 2) Fenced code blocks.
    fence = "```"
    parts = text.split(fence)
    # Odd indices are fenced bodies when the reply opens outside a fence.
    for i in range(1, len(parts), 2):
        body = parts[i]
        if body.startswith("json"):
            body = body[4:]
        body = body.strip()
        if not body.startswith("{"):
            continue
        try:
            found = _tool_calls_from_payload(json.loads(body))
        except json.JSONDecodeError:
            continue
        if found is not None:
            return found

    # 3) First balanced object that contains "tool_calls".
    needle = '"tool_calls"'
    search_from = 0
    while True:
        hit = text.find(needle, search_from)
        if hit < 0:
            break
        brace = text.rfind("{", 0, hit)
        while brace >= 0:
            blob = _extract_balanced_json_object(text, brace)
            if blob is not None:
                try:
                    found = _tool_calls_from_payload(json.loads(blob))
                except json.JSONDecodeError:
                    found = None
                if found is not None:
                    return found
            brace = text.rfind("{", 0, brace)
        search_from = hit + len(needle)
    return []


def _strip_tool_calls_from_reply(reply: str) -> str:
    """Remove tool_calls JSON (and wrapping fences) from the assistant reply."""
    fence = "```"
    parts = reply.split(fence)
    rebuilt: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            rebuilt.append(part)
            continue
        content = part[4:] if part.startswith("json") else part
        content_stripped = content.strip()
        is_tool_block = False
        if content_stripped.startswith("{"):
            try:
                is_tool_block = (
                    _tool_calls_from_payload(json.loads(content_stripped)) is not None
                )
            except json.JSONDecodeError:
                is_tool_block = False
        if is_tool_block:
            continue
        rebuilt.append(fence)
        rebuilt.append(part)
        rebuilt.append(fence)
    text = "".join(rebuilt)

    lines_out: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped.startswith("{") and '"tool_calls"' in stripped:
            try:
                if _tool_calls_from_payload(json.loads(stripped)) is not None:
                    continue
            except json.JSONDecodeError:
                pass
        lines_out.append(ln)
    text = "\n".join(lines_out)

    needle = '"tool_calls"'
    hit = text.find(needle)
    if hit >= 0:
        brace = text.rfind("{", 0, hit)
        while brace >= 0:
            blob = _extract_balanced_json_object(text, brace)
            if blob is not None:
                try:
                    if _tool_calls_from_payload(json.loads(blob)) is not None:
                        text = (text[:brace] + text[brace + len(blob) :]).strip()
                        break
                except json.JSONDecodeError:
                    pass
            brace = text.rfind("{", 0, brace)

    return text.strip()


def run_coaching_turn(
    *,
    user_id: str,
    user_message: str,
    dashboard_context: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    llm: LLMClient,
    guidelines: GuidelinesContext | None = None,
    query_all: QueryAll | None = None,
) -> dict[str, Any]:
    """One chat turn: LLM reply + optional validated tool invocations.

    Two-step for reads: when the model calls ``query_history`` and a ``query_all``
    executor is supplied, the bounded SELECT runs and its rows are summarized into
    the visible reply (text-to-SQL folded into the chat). Write tools are validated
    into ``pending_writes`` for the caller to persist, exactly as before.
    """
    prompt = format_chat_prompt(
        dashboard_context=dashboard_context,
        messages=messages,
        user_message=user_message,
        guidelines=guidelines,
    )
    reply = llm(CHAT_SYSTEM, prompt).strip()

    tool_calls = extract_tool_calls(reply)
    tool_results: list[dict[str, Any]] = []
    pending_writes: list[dict[str, Any]] = []
    query_results: list[dict[str, Any]] = []
    history_answers: list[str] = []
    for tc in tool_calls:
        name = tc.get("name")
        args = tc.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        if name == QUERY_HISTORY_TOOL:
            # One read per turn bounds LLM+DB fan-out from a single model reply.
            if query_all is None or query_results:
                continue
            question = str(args.get("question") or user_message).strip()
            result = run_history_query(
                question, user_id=user_id, llm=llm, query_all=query_all
            )
            query_results.append(result)
            if result["ok"]:
                history_answers.append(
                    summarize_query_result(
                        question, result["sql"], result["rows"], llm=llm
                    )
                )
            else:
                history_answers.append(f"I couldn't run that lookup: {result['error']}")
            continue
        try:
            write = apply_tool_call(name, args, user_id=user_id)
            tool_results.append({"tool": name, "ok": True, "result": write})
            pending_writes.append(write)
        except (ValueError, TypeError) as exc:
            tool_results.append({"tool": name, "ok": False, "error": str(exc)})

    # Strip tool JSON from visible prose, then append history answers.
    visible = _strip_tool_calls_from_reply(reply)
    answer = "\n\n".join(a for a in history_answers if a.strip())
    if answer:
        visible = f"{visible}\n\n{answer}".strip() if visible else answer
    if not visible:
        # Never surface the raw tool-call JSON; writes are confirmed by the caller.
        visible = "Done." if tool_calls else reply
    return {
        "reply": visible,
        "tool_results": tool_results,
        "pending_writes": pending_writes,
        "query_results": query_results,
    }


def load_chat_messages(
    conn: Any,
    *,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, str]]:
    """Load recent coaching chat rows for one user."""
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT role, content FROM coaching_chat_messages "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def save_chat_messages(
    conn: Any,
    *,
    user_id: str,
    messages: Sequence[tuple[str, str]],
) -> None:
    """Persist new chat messages (role, content pairs)."""
    if not messages:
        return
    with conn.cursor() as cur:
        for role, content in messages:
            cur.execute(
                "INSERT INTO coaching_chat_messages (user_id, role, content) "
                "VALUES (%s, %s, %s)",
                (user_id, role, content),
            )

"""Slice C.1 schema-bound text-to-SQL for history questions.

The LLM generates a single SELECT from a natural-language question; validation
ensures read-only access to allow-listed tables with a ``user_id`` filter before
execution.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pipeline.briefing import LLMClient
from pipeline.dashboard_queries import (
    ALLOWED_QUERY_TABLES,
    BOUNDED_SCHEMA_HINT,
    validate_bounded_sql,
)

SQL_GEN_SYSTEM = (
    "You write PostgreSQL SELECT queries for a personal health database. "
    "Respond with ONLY a single SQL SELECT statement — no markdown, no explanation. "
    "Rules:\n"
    "- Only SELECT (read-only).\n"
    "- Always filter by user_id = '{user_id}'.\n"
    "- Only use tables from the schema hint.\n"
    "- LIMIT 500 or fewer rows.\n"
    "- Prefer aggregates (AVG, COUNT, SUM) for trends.\n"
    "- Use ISO dates (YYYY-MM-DD) for date literals.\n"
)

_QUERY_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql_from_llm(text: str) -> str:
    """Pull a SQL statement from raw LLM output."""
    cleaned = text.strip()
    match = _QUERY_FENCE.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    # Drop leading commentary before SELECT
    lower = cleaned.lower()
    idx = lower.find("select")
    if idx > 0:
        cleaned = cleaned[idx:]
    return cleaned.strip().rstrip(";")


QueryAll = Callable[[str, tuple[Any, ...]], Sequence[Mapping[str, Any]]]


def generate_bounded_sql(
    question: str,
    *,
    user_id: str,
    llm: LLMClient,
) -> str:
    """Use the injected LLM to produce a validated SELECT for one user."""
    system = SQL_GEN_SYSTEM.format(user_id=user_id)
    prompt = (
        f"SCHEMA:\n{BOUNDED_SCHEMA_HINT}\n\n"
        f"USER_ID (must appear in WHERE): {user_id}\n\n"
        f"QUESTION: {question.strip()}\n\n"
        "SQL:"
    )
    raw = llm(system, prompt)
    sql = extract_sql_from_llm(raw)
    return validate_bounded_sql(sql, user_id=user_id)


def execute_bounded_query(
    sql: str,
    *,
    user_id: str,
    query_all: QueryAll,
) -> list[dict[str, Any]]:
    """Validate and run a bounded SELECT; return rows as plain dicts."""
    safe_sql = validate_bounded_sql(sql, user_id=user_id)
    rows = query_all(safe_sql, ())
    return [dict(r) for r in rows]


def run_history_query(
    question: str,
    *,
    user_id: str,
    llm: LLMClient,
    query_all: QueryAll,
) -> dict[str, Any]:
    """End-to-end: NL question → SQL → validated execution."""
    try:
        sql = generate_bounded_sql(question, user_id=user_id, llm=llm)
        rows = execute_bounded_query(sql, user_id=user_id, query_all=query_all)
        return {"ok": True, "sql": sql, "rows": rows, "row_count": len(rows)}
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc), "sql": None, "rows": [], "row_count": 0}


def summarize_query_result(
    question: str,
    sql: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    llm: LLMClient,
) -> str:
    """Short natural-language summary of query results for the chat UI."""
    preview = json.dumps(list(rows)[:20], indent=2, default=str)
    if len(rows) > 20:
        preview += f"\n... ({len(rows) - 20} more rows)"
    prompt = (
        f"The athlete asked: {question.strip()}\n\n"
        f"SQL executed:\n{sql}\n\n"
        f"Results ({len(rows)} rows):\n{preview}\n\n"
        "Summarize the answer in 2-4 sentences. Cite numbers from the results only."
    )
    return llm(
        "You explain health data query results briefly and accurately.",
        prompt,
    ).strip()

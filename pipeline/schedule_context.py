"""Calendar-aware schedule context (Slice D).

Reads ``schedule_exceptions`` and optional calendar busy blocks (from
``interventions`` with category ``calendar_busy``) to adjust today's focus
without LLM freestyle replanning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

RUNNING_GOAL_TYPES = frozenset(
    {
        "running",
        # Legacy typed goals — still match schedule exceptions that reference them.
        "running_long",
        "running_easy",
        "running_interval",
    }
)


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def active_schedule_exceptions(
    *,
    run_date: date,
    exceptions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return exceptions covering ``run_date``."""
    active: list[dict[str, Any]] = []
    for ex in exceptions:
        start = _parse_date(ex.get("start_date"))
        end = _parse_date(ex.get("end_date"))
        if start is None or end is None:
            continue
        if start <= run_date <= end:
            active.append(dict(ex))
    return active


def is_goal_blocked(
    goal_type: str,
    *,
    run_date: date,
    exceptions: Sequence[Mapping[str, Any]],
) -> str | None:
    """If ``goal_type`` is blocked today, return override hint or reason."""
    for ex in active_schedule_exceptions(run_date=run_date, exceptions=exceptions):
        affected = ex.get("affected_goal_types") or []
        if goal_type not in affected:
            continue
        hint = ex.get("override_hint") or ex.get("reason")
        if isinstance(hint, str) and hint.strip():
            return hint.strip()
        return "schedule exception active"
    return None


def calendar_busy_today(
    *,
    run_date: date,
    interventions: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Summaries of calendar busy blocks on ``run_date``."""
    blocks: list[str] = []
    for row in interventions:
        if row.get("category") != "calendar_busy":
            continue
        ed = _parse_date(row.get("event_date"))
        if ed == run_date:
            desc = row.get("description")
            if isinstance(desc, str) and desc.strip():
                blocks.append(desc.strip())
    return blocks


def apply_schedule_to_focus_parts(
    focus_parts: list[str],
    *,
    run_date: date,
    exceptions: Sequence[Mapping[str, Any]],
    interventions: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Augment or replace focus parts when schedule constraints apply."""
    if not focus_parts and not exceptions:
        return focus_parts
    busy = calendar_busy_today(run_date=run_date, interventions=interventions or ())
    if busy:
        focus_parts.append(f"Calendar busy: {busy[0][:80]}")
    for ex in active_schedule_exceptions(run_date=run_date, exceptions=exceptions):
        hint = ex.get("override_hint")
        if isinstance(hint, str) and hint.strip():
            focus_parts.append(hint.strip())
    return focus_parts

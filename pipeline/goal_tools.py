"""Natural-language goal control plane (Slice B).

Parses user intent into validated :class:`GoalPatch` objects and applies
structured writes (same paths as Slice A). Fixed tool list — not an
unconstrained agent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pipeline.briefing import LLMClient
from pipeline.training_phase import training_phase_row
from pipeline.units import miles_to_km

VALID_GOAL_TYPES = frozenset(
    {
        "strength",
        "running_long",
        "running_easy",
        "running_interval",
    }
)

PARSE_SYSTEM = (
    "You extract structured goal updates from athlete chat. Respond with ONLY "
    "valid JSON: {\"patches\": [{\"goal_type\": \"strength|running_long|...\", "
    "\"target_min\": int|null, \"target_max\": int|null, \"target_label\": str|null, "
    "\"deactivate\": bool, \"notes\": str|null}], "
    "\"narrative_note\": str|null}. One patch per goal_type max. "
    "Do not invent goal types outside the allowed set."
)


@dataclass(frozen=True, slots=True)
class GoalPatch:
    """Validated structured mutation for one goal."""

    goal_type: str
    target_min: int | None = None
    target_max: int | None = None
    target_label: str | None = None
    deactivate: bool = False
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    patches: list[GoalPatch] = field(default_factory=list)
    narrative_note: str | None = None
    needs_confirmation: bool = False
    confirmation_message: str | None = None


def validate_patch(raw: Mapping[str, Any]) -> GoalPatch:
    """Validate a single patch dict; raise ValueError on bad input."""
    gtype = raw.get("goal_type")
    if not isinstance(gtype, str) or gtype not in VALID_GOAL_TYPES:
        raise ValueError(f"Invalid goal_type: {gtype!r}")
    deactivate = bool(raw.get("deactivate", False))
    tmin = raw.get("target_min")
    tmax = raw.get("target_max")
    if tmin is not None:
        tmin = int(tmin)
    if tmax is not None:
        tmax = int(tmax)
    label = raw.get("target_label")
    if label is not None and not isinstance(label, str):
        raise ValueError("target_label must be a string")
    notes = raw.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("notes must be a string")
    if not deactivate and tmin is None and tmax is None and not label:
        raise ValueError("Patch must set targets or deactivate")
    return GoalPatch(
        goal_type=gtype,
        target_min=tmin,
        target_max=tmax,
        target_label=label,
        deactivate=deactivate,
        notes=notes,
    )


def parse_goal_patches_from_json(text: str) -> ParseResult:
    """Parse LLM JSON output into validated patches."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    raw_patches = payload.get("patches") or []
    if not isinstance(raw_patches, list):
        raise ValueError("patches must be a list")
    if len(raw_patches) > 3:
        raise ValueError("Too many goal patches in one request (max 3)")
    patches = [validate_patch(p) for p in raw_patches if isinstance(p, dict)]
    narrative = payload.get("narrative_note")
    if narrative is not None and not isinstance(narrative, str):
        narrative = None
    needs_confirm = len(patches) > 1 or any(p.deactivate for p in patches)
    confirm_msg = None
    if needs_confirm and patches:
        parts = [f"{p.goal_type}: deactivate={p.deactivate}" for p in patches]
        confirm_msg = f"Confirm goal changes: {', '.join(parts)}?"
    return ParseResult(
        patches=patches,
        narrative_note=narrative,
        needs_confirmation=needs_confirm,
        confirmation_message=confirm_msg,
    )


def parse_goal_message(
    message: str,
    *,
    llm: LLMClient,
) -> ParseResult:
    """Use the injected LLM to extract structured goal patches."""
    raw = llm(PARSE_SYSTEM, f"Athlete message:\n{message.strip()}\n\nJSON:")
    return parse_goal_patches_from_json(raw)


def goal_patch_to_row(user_id: str, patch: GoalPatch) -> dict[str, Any]:
    """Map a patch to a ``goals`` upsert row."""
    row: dict[str, Any] = {
        "user_id": user_id,
        "goal_type": patch.goal_type,
        "is_active": not patch.deactivate,
        "period": "weekly",
    }
    if patch.target_min is not None:
        row["target_min"] = patch.target_min
    if patch.target_max is not None:
        row["target_max"] = patch.target_max
    if patch.target_label is not None:
        row["target_label"] = patch.target_label
    if patch.notes is not None:
        row["notes"] = patch.notes
    return row


def log_run_row(
    *,
    user_id: str,
    session_date: date,
    run_type: str,
    distance_miles: float | None = None,
    duration_min: float | None = None,
    notes: str | None = None,
    source: str = "manual",
    source_id: str | None = None,
) -> dict[str, Any]:
    """Build a ``running_sessions`` insert row.

    The athlete-facing interface is miles; ``running_sessions`` stores the base
    metric unit, so the miles input is converted to ``distance_km`` here.
    """
    if run_type not in ("long", "easy", "interval"):
        raise ValueError(f"Invalid run_type: {run_type!r}")
    sid = source_id or f"{source}:{session_date.isoformat()}:{run_type}"
    row: dict[str, Any] = {
        "user_id": user_id,
        "session_date": session_date,
        "run_type": run_type,
        "source": source,
        "source_id": sid,
    }
    if distance_miles is not None:
        row["distance_km"] = miles_to_km(distance_miles)
    if duration_min is not None:
        row["duration_min"] = duration_min
    if notes is not None:
        row["notes"] = notes
    return row


# Tool schemas shared with coaching chat (Slice C).
COACHING_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "update_goal",
        "description": "Update a structured weekly goal target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_type": {"type": "string", "enum": sorted(VALID_GOAL_TYPES)},
                "target_min": {"type": "integer"},
                "target_max": {"type": "integer"},
                "target_label": {"type": "string"},
                "deactivate": {"type": "boolean"},
            },
            "required": ["goal_type"],
        },
    },
    {
        "name": "log_run",
        "description": "Log a running session (long, easy, or interval).",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_date": {"type": "string", "format": "date"},
                "run_type": {"type": "string", "enum": ["long", "easy", "interval"]},
                "distance_miles": {"type": "number"},
                "duration_min": {"type": "number"},
                "notes": {"type": "string"},
            },
            "required": ["session_date", "run_type"],
        },
    },
    {
        "name": "append_goal_note",
        "description": "Free-text note for narrative goals file (Phase 10).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "query_history",
        "description": (
            "Answer a question about the athlete's historical data (trends, "
            "averages, counts over time — sleep, HRV, resting HR, body weight, "
            "cardio, strength) by running a read-only, schema-bound SQL query. "
            "Use this whenever the answer is not already in DASHBOARD_CONTEXT. "
            "Provide a clear natural-language 'question'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "set_schedule_exception",
        "description": "Block or reschedule goal types for a date range (Slice D).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "affected_goal_types": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "override_hint": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["start_date", "end_date", "affected_goal_types"],
        },
    },
    {
        "name": "set_training_phase",
        "description": (
            "Schedule a multi-week training block (building, deload, fat_loss, running, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phase_type": {"type": "string"},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "notes": {"type": "string"},
                "target_notes": {"type": "string"},
            },
            "required": ["name", "phase_type", "start_date", "end_date"],
        },
    },
]


def apply_tool_call(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    user_id: str,
) -> dict[str, Any]:
    """Validate a tool invocation and return a row dict or note payload."""
    if tool_name == "update_goal":
        patch = validate_patch(arguments)
        return {"action": "upsert_goal", "row": goal_patch_to_row(user_id, patch)}
    if tool_name == "log_run":
        sd = arguments.get("session_date")
        if not isinstance(sd, str):
            raise ValueError("session_date required")
        run_type = arguments.get("run_type")
        if not isinstance(run_type, str):
            raise ValueError("run_type required")
        row = log_run_row(
            user_id=user_id,
            session_date=date.fromisoformat(sd[:10]),
            run_type=run_type,
            distance_miles=arguments.get("distance_miles"),
            duration_min=arguments.get("duration_min"),
            notes=arguments.get("notes"),
        )
        return {"action": "insert_running_session", "row": row}
    if tool_name == "append_goal_note":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text required")
        return {"action": "append_goal_note", "text": text.strip()}
    if tool_name == "set_schedule_exception":
        start = arguments.get("start_date")
        end = arguments.get("end_date")
        affected = arguments.get("affected_goal_types")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("start_date and end_date required")
        if not isinstance(affected, list) or not affected:
            raise ValueError("affected_goal_types required")
        return {
            "action": "insert_schedule_exception",
            "row": {
                "user_id": user_id,
                "start_date": date.fromisoformat(start[:10]),
                "end_date": date.fromisoformat(end[:10]),
                "affected_goal_types": [str(a) for a in affected],
                "override_hint": arguments.get("override_hint"),
                "reason": arguments.get("reason"),
            },
        }
    if tool_name == "set_training_phase":
        name = arguments.get("name")
        phase_type = arguments.get("phase_type")
        start = arguments.get("start_date")
        end = arguments.get("end_date")
        if not isinstance(name, str) or not isinstance(phase_type, str):
            raise ValueError("name and phase_type required")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("start_date and end_date required")
        row = training_phase_row(
            user_id=user_id,
            name=name,
            phase_type=phase_type,
            start_date=date.fromisoformat(start[:10]),
            end_date=date.fromisoformat(end[:10]),
            notes=arguments.get("notes") if isinstance(arguments.get("notes"), str) else None,
            target_notes=(
                arguments.get("target_notes")
                if isinstance(arguments.get("target_notes"), str)
                else None
            ),
        )
        return {"action": "insert_training_phase", "row": row}
    raise ValueError(f"Unknown tool: {tool_name!r}")


def apply_coaching_writes(
    cur: Any,
    pending_writes: Sequence[Mapping[str, Any]],
    *,
    append_note: Callable[[str], str] | None = None,
) -> list[str]:
    """Persist validated coaching chat tool results to Postgres / guidelines.

    ``append_note`` handles ``append_goal_note`` when Phase 10 storage is wired.
    """
    from pipeline import persistence

    applied: list[str] = []
    for write in pending_writes:
        action = write.get("action")
        if action == "upsert_goal":
            row = write.get("row")
            if not isinstance(row, Mapping):
                continue
            persistence.upsert_row(cur, "goals", row)
            applied.append(f"Updated goal: {row.get('goal_type')}")
        elif action == "insert_running_session":
            row = write.get("row")
            if not isinstance(row, Mapping):
                continue
            persistence.insert_running_session(cur, row)
            applied.append(
                f"Logged run: {row.get('run_type')} on {row.get('session_date')}"
            )
        elif action == "insert_schedule_exception":
            row = write.get("row")
            if not isinstance(row, Mapping):
                continue
            persistence.insert_schedule_exception(cur, row)
            applied.append(
                f"Schedule exception {row.get('start_date')}–{row.get('end_date')}"
            )
        elif action == "insert_training_phase":
            row = write.get("row")
            if not isinstance(row, Mapping):
                continue
            persistence.insert_training_phase(cur, row)
            applied.append(
                f"Training phase {row.get('name')} ({row.get('start_date')}–{row.get('end_date')})"
            )
        elif action == "append_goal_note":
            text = write.get("text")
            if isinstance(text, str) and append_note is not None:
                applied.append(append_note(text))
    return applied

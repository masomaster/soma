"""Longer-term training phase schedules (building, deload, fat loss, running).

Phases are stored in ``training_phases`` and surfaced in the dashboard and
briefing as pre-computed context — the LLM narrates; it does not invent plans.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def active_training_phase(
    phases: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any] | None:
    """Return the phase covering ``as_of``, preferring the most recently started."""
    matches: list[Mapping[str, Any]] = []
    for row in phases:
        if row.get("is_active") is False:
            continue
        start = _parse_date(row.get("start_date"))
        end = _parse_date(row.get("end_date"))
        if start is None or end is None:
            continue
        if start <= as_of <= end:
            matches.append(row)
    if not matches:
        return None
    matches.sort(key=lambda r: _parse_date(r.get("start_date")) or date.min, reverse=True)
    return dict(matches[0])


def upcoming_training_phases(
    phases: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Future phases sorted by start date."""
    rows: list[dict[str, Any]] = []
    for row in phases:
        if row.get("is_active") is False:
            continue
        start = _parse_date(row.get("start_date"))
        end = _parse_date(row.get("end_date"))
        if start is None or end is None or start <= as_of:
            continue
        rows.append(dict(row))
    rows.sort(key=lambda r: _parse_date(r.get("start_date")) or date.max)
    return rows[:limit]


def phase_progress(phase: Mapping[str, Any], *, as_of: date) -> dict[str, Any]:
    """Weeks elapsed/remaining and completion percentage for a phase."""
    start = _parse_date(phase.get("start_date"))
    end = _parse_date(phase.get("end_date"))
    if start is None or end is None or end < start:
        return {"weeks_total": None, "weeks_elapsed": None, "weeks_remaining": None, "pct_complete": None}
    total_days = (end - start).days + 1
    elapsed_days = max(0, min((as_of - start).days + 1, total_days))
    remaining_days = max(0, (end - as_of).days)
    weeks_total = max(1, (total_days + 6) // 7)
    weeks_elapsed = min(weeks_total, (elapsed_days + 6) // 7)
    weeks_remaining = max(0, weeks_total - weeks_elapsed)
    pct = round(elapsed_days / total_days * 100.0, 1) if total_days else None
    return {
        "weeks_total": weeks_total,
        "weeks_elapsed": weeks_elapsed,
        "weeks_remaining": weeks_remaining,
        "pct_complete": pct,
        "days_remaining": remaining_days,
    }


def build_training_phase_context(
    phases: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    """Shape phase rows for dashboard context and briefing prompts."""
    active = active_training_phase(phases, as_of=as_of)
    upcoming = upcoming_training_phases(phases, as_of=as_of)
    ctx: dict[str, Any] = {"as_of": as_of.isoformat(), "active": None, "upcoming": []}
    if active:
        start = _parse_date(active.get("start_date"))
        end = _parse_date(active.get("end_date"))
        progress = phase_progress(active, as_of=as_of)
        ctx["active"] = {
            "name": active.get("name"),
            "phase_type": active.get("phase_type"),
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "notes": active.get("notes"),
            "target_notes": active.get("target_notes"),
            **progress,
        }
    for row in upcoming:
        start = _parse_date(row.get("start_date"))
        end = _parse_date(row.get("end_date"))
        ctx["upcoming"].append(
            {
                "name": row.get("name"),
                "phase_type": row.get("phase_type"),
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "notes": row.get("notes"),
            }
        )
    return ctx


def training_phase_row(
    *,
    user_id: str,
    name: str,
    phase_type: str,
    start_date: date,
    end_date: date,
    notes: str | None = None,
    target_notes: str | None = None,
) -> dict[str, Any]:
    """Validated insert payload for ``training_phases``."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    clean_type = phase_type.strip().lower().replace(" ", "_")
    if not clean_type:
        raise ValueError("phase_type required")
    if not name.strip():
        raise ValueError("name required")
    return {
        "user_id": user_id,
        "name": name.strip(),
        "phase_type": clean_type,
        "start_date": start_date,
        "end_date": end_date,
        "notes": notes,
        "target_notes": target_notes,
        "is_active": True,
    }

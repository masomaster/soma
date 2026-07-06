"""Athlete journal entries — subjective notes managed via coaching chat.

Examples: workout difficulty, supplement changes, recovery observations.
Pure formatting helpers live here; persistence is via ``goal_tools`` / Postgres.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

JOURNAL_CATEGORIES = frozenset(
    {
        "workout",
        "supplement",
        "recovery",
        "training",
        "general",
    }
)


def journal_entry_row(
    *,
    user_id: str,
    body: str,
    entry_date: date,
    category: str = "general",
) -> dict[str, Any]:
    """Validated insert payload for ``athlete_journal_entries``."""
    text = body.strip()
    if not text:
        raise ValueError("body required")
    cat = category.strip().lower().replace(" ", "_")
    if cat not in JOURNAL_CATEGORIES:
        raise ValueError(f"Invalid category: {category!r}")
    return {
        "user_id": user_id,
        "entry_date": entry_date,
        "category": cat,
        "body": text,
    }


def format_journal_for_prompt(
    entries: Sequence[Mapping[str, Any]] | None,
    *,
    max_entries: int = 25,
) -> list[dict[str, Any]]:
    """Shape recent journal rows for LLM context (newest first)."""
    shaped: list[dict[str, Any]] = []
    for row in entries or ():
        entry_date = row.get("entry_date")
        if hasattr(entry_date, "isoformat"):
            entry_date = entry_date.isoformat()
        created = row.get("created_at")
        if hasattr(created, "isoformat"):
            created = created.isoformat()
        shaped.append(
            {
                "id": str(row.get("id")) if row.get("id") is not None else None,
                "entry_date": entry_date,
                "category": row.get("category"),
                "body": row.get("body"),
                "logged_at": created,
            }
        )
        if len(shaped) >= max_entries:
            break
    return shaped

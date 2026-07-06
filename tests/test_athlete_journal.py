"""Tests for athlete journal entries."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.athlete_journal import format_journal_for_prompt, journal_entry_row


def test_journal_entry_row_validates_category():
    row = journal_entry_row(
        user_id="u1",
        body="Chest press felt heavy.",
        entry_date=date(2024, 6, 8),
        category="workout",
    )
    assert row["category"] == "workout"


def test_journal_entry_row_rejects_empty_body():
    with pytest.raises(ValueError, match="body"):
        journal_entry_row(user_id="u1", body="  ", entry_date=date(2024, 6, 8))


def test_format_journal_for_prompt_limits_entries():
    rows = [
        {"id": i, "entry_date": date(2024, 6, i), "category": "general", "body": f"note {i}"}
        for i in range(1, 6)
    ]
    shaped = format_journal_for_prompt(rows, max_entries=3)
    assert len(shaped) == 3

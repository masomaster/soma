"""Tests for training phase scheduling."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.training_phase import (
    active_training_phase,
    build_training_phase_context,
    phase_progress,
    training_phase_row,
)


def test_active_training_phase_prefers_current_block():
    phases = [
        {
            "name": "Building",
            "phase_type": "building",
            "start_date": date(2024, 6, 1),
            "end_date": date(2024, 7, 15),
            "is_active": True,
        },
        {
            "name": "Future cut",
            "phase_type": "fat_loss",
            "start_date": date(2024, 8, 1),
            "end_date": date(2024, 8, 28),
            "is_active": True,
        },
    ]
    active = active_training_phase(phases, as_of=date(2024, 6, 10))
    assert active is not None
    assert active["name"] == "Building"


def test_phase_progress_weeks():
    phase = {
        "start_date": date(2024, 6, 1),
        "end_date": date(2024, 7, 12),
    }
    progress = phase_progress(phase, as_of=date(2024, 6, 15))
    assert progress["weeks_total"] is not None
    assert progress["weeks_elapsed"] >= 1
    assert progress["pct_complete"] is not None


def test_training_phase_row_rejects_inverted_dates():
    with pytest.raises(ValueError, match="end_date"):
        training_phase_row(
            user_id="u1",
            name="Bad",
            phase_type="building",
            start_date=date(2024, 6, 10),
            end_date=date(2024, 6, 1),
        )


def test_build_training_phase_context_includes_upcoming():
    phases = [
        {
            "name": "Build",
            "phase_type": "building",
            "start_date": date(2024, 6, 1),
            "end_date": date(2024, 6, 30),
            "is_active": True,
        },
        {
            "name": "Deload",
            "phase_type": "deload",
            "start_date": date(2024, 7, 1),
            "end_date": date(2024, 7, 7),
            "is_active": True,
        },
    ]
    ctx = build_training_phase_context(phases, as_of=date(2024, 6, 10))
    assert ctx["active"]["name"] == "Build"
    assert len(ctx["upcoming"]) == 1
    assert ctx["upcoming"][0]["name"] == "Deload"

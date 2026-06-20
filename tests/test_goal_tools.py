"""Tests for Slice B goal tools."""

from __future__ import annotations

import json
from datetime import date

import pytest

from pipeline.goal_tools import (
    apply_coaching_writes,
    apply_tool_call,
    parse_goal_patches_from_json,
    validate_patch,
)


def test_validate_patch_strength():
    p = validate_patch({"goal_type": "strength", "target_min": 3, "target_max": 4})
    assert p.goal_type == "strength"
    assert p.target_min == 3


def test_validate_patch_rejects_unknown_type():
    with pytest.raises(ValueError, match="Invalid goal_type"):
        validate_patch({"goal_type": "yoga", "target_min": 1})


def test_parse_goal_patches_from_json():
    raw = json.dumps(
        {
            "patches": [{"goal_type": "running_easy", "target_min": 2}],
            "narrative_note": "More easy miles",
        }
    )
    result = parse_goal_patches_from_json(raw)
    assert len(result.patches) == 1
    assert result.narrative_note == "More easy miles"


def test_apply_tool_log_run():
    out = apply_tool_call(
        "log_run",
        {"session_date": "2024-06-08", "run_type": "long", "distance_km": 16},
        user_id="u1",
    )
    assert out["action"] == "insert_running_session"
    assert out["row"]["run_type"] == "long"


def test_apply_tool_schedule_exception():
    out = apply_tool_call(
        "set_schedule_exception",
        {
            "start_date": "2024-06-10",
            "end_date": "2024-06-12",
            "affected_goal_types": ["running_interval"],
            "override_hint": "Skip intervals — travel",
        },
        user_id="u1",
    )
    assert out["action"] == "insert_schedule_exception"
    assert out["row"]["start_date"] == date(2024, 6, 10)


def test_apply_coaching_writes_upsert_goal():
    class _Cur:
        def execute(self, *args: object, **kwargs: object) -> None:
            pass

    from unittest.mock import patch

    cur = _Cur()
    pending = [
        apply_tool_call(
            "update_goal",
            {"goal_type": "strength", "target_min": 2, "target_max": 3},
            user_id="u1",
        )
    ]
    with patch("pipeline.persistence.upsert_row") as upsert:
        applied = apply_coaching_writes(cur, pending)
        upsert.assert_called_once()
        assert applied == ["Updated goal: strength"]

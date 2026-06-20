"""Tests for Phase 10 guidelines loader."""

from __future__ import annotations

from pathlib import Path

from pipeline.guidelines import (
    append_goal_note,
    format_guidelines_for_prompt,
    guideline_object_key,
    load_guidelines,
    local_guidelines_storage,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "guidelines"
UID = "demo-user"


def test_guideline_object_key():
    assert guideline_object_key(UID, "my-goals.md") == f"guidelines/{UID}/my-goals.md"


def test_load_guidelines_from_fixture_dir():
    get_object, _ = local_guidelines_storage(FIXTURE_ROOT)
    ctx = load_guidelines(UID, get_object=get_object)
    assert ctx.my_goals and "strength" in ctx.my_goals.lower()
    assert ctx.injury_history and "shoulder" in ctx.injury_history.lower()


def test_format_guidelines_for_prompt():
    get_object, _ = local_guidelines_storage(FIXTURE_ROOT)
    ctx = load_guidelines(UID, get_object=get_object)
    blob = format_guidelines_for_prompt(ctx)
    assert "INJURY HISTORY" in blob
    assert "shoulder" in blob.lower()


def test_append_goal_note_creates_file(tmp_path: Path):
    get_object, put_object = local_guidelines_storage(tmp_path)
    msg = append_goal_note(
        "u1",
        "Focusing on easy miles this week.",
        get_object=get_object,
        put_object=put_object,
    )
    assert "my-goals" in msg
    key = guideline_object_key("u1", "my-goals.md")
    body = get_object(key)
    assert body is not None
    assert b"easy miles" in body

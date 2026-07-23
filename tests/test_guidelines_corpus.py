"""Tests for Phase 10 expert-principles corpus condensation."""

from pathlib import Path

import pytest

from pipeline.guidelines_corpus import (
    CONDENSE_SYSTEM,
    TARGET_EXPERT_PRINCIPLES_CHARS,
    build_condense_user_prompt,
    condense_transcripts,
    discover_transcripts,
    load_transcript,
    skeleton_expert_principles,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "guidelines-transcripts"


def test_load_transcript_with_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "nippard-overload.md"
    path.write_text(
        "---\n"
        "source: Jeff Nippard\n"
        "title: Progressive Overload\n"
        "url: https://example.com/video\n"
        "date: 2024-06-01\n"
        "---\n"
        "\n"
        "Add load or reps over time when form is solid.\n",
        encoding="utf-8",
    )
    doc = load_transcript(path)
    assert doc.source == "Jeff Nippard"
    assert doc.title == "Progressive Overload"
    assert "Add load or reps" in doc.body
    assert "Jeff Nippard" in doc.label()


def test_discover_and_build_prompt() -> None:
    docs = discover_transcripts(FIXTURE_DIR)
    assert len(docs) >= 1
    prompt = build_condense_user_prompt(docs)
    assert "TRANSCRIPT 1" in prompt
    assert "hard sets" in prompt.lower() or "volume" in prompt.lower()
    assert str(TARGET_EXPERT_PRINCIPLES_CHARS) in prompt


def test_condense_transcripts_uses_llm() -> None:
    docs = discover_transcripts(FIXTURE_DIR)

    def fake_llm(system: str, user: str) -> str:
        assert system == CONDENSE_SYSTEM
        assert "TRANSCRIPT" in user
        return (
            "# Expert Training Principles\n\n"
            "## Volume\n\n- Ten hard sets can be enough to start.\n"
        )

    draft = condense_transcripts(docs, llm=fake_llm)
    assert draft.startswith("# Expert Training Principles")
    assert "Ten hard sets" in draft


def test_skeleton_fits_prompt_budget() -> None:
    text = skeleton_expert_principles()
    assert text.startswith("# Expert Training Principles")
    assert len(text) <= TARGET_EXPERT_PRINCIPLES_CHARS
    assert "Israetel" in text or "RP" in text


def test_discover_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_transcripts(tmp_path / "missing")

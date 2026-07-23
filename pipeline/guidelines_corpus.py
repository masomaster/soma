"""One-time expert-principles corpus builder (Phase 10).

Operators drop manually obtained YouTube transcripts (captions they own /
exported officially — no ToS-violating scrapers) into a directory. This module
parses optional YAML frontmatter, builds a condensation prompt, and optionally
calls an injected LLM to draft ``expert-principles.md`` bullets for human review
before S3 upload.

See ``scripts/guidelines-corpus.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pipeline.guidelines import DEFAULT_MAX_CHARS

logger = logging.getLogger(__name__)

TRANSCRIPT_SUFFIXES = (".md", ".txt", ".markdown")

# Soft budget for the synthesized file (prompt injection truncates at DEFAULT_MAX_CHARS).
TARGET_EXPERT_PRINCIPLES_CHARS = DEFAULT_MAX_CHARS

CONDENSE_SYSTEM = (
    "You distill science-based lifting / cardio coaching transcripts into a "
    "compact markdown guidelines document for a personal health coach (Soma). "
    "Rules:\n"
    "- Output ONLY markdown for expert-principles.md (no preamble).\n"
    "- Start with '# Expert Training Principles'.\n"
    "- Group into short ## sections (Volume, Intensity/Proximity to Failure, "
    "Progressive Overload, Recovery/Deloads, Technique/Injury Prevention, "
    "Cardio as relevant).\n"
    "- Prefer actionable bullets a coach can cite; attribute source in the "
    "section heading or parenthetical (e.g. RP / Israetel, Nippard, Ethier).\n"
    "- Deduplicate overlapping advice; prefer consensus over one-off claims.\n"
    "- Do NOT invent studies, numbers, or claims absent from the transcripts.\n"
    "- Do NOT copy long verbatim quotes — paraphrase into coaching bullets.\n"
    "- Keep the whole document under "
    f"{TARGET_EXPERT_PRINCIPLES_CHARS} characters so it fits prompt injection.\n"
    "- End with a short '## Sources' list of title + creator from the inputs."
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class TranscriptDoc:
    """One manually supplied transcript file."""

    path: Path
    body: str
    source: str | None = None
    title: str | None = None
    url: str | None = None
    date: str | None = None

    def label(self) -> str:
        bits = [b for b in (self.source, self.title) if b]
        if bits:
            return " — ".join(bits)
        return self.path.stem.replace("-", " ").replace("_", " ")


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw.strip())
    if not match:
        return {}, raw.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key and value:
            meta[key] = value
    return meta, match.group(2).strip()


def load_transcript(path: Path) -> TranscriptDoc:
    """Load one transcript markdown/text file with optional YAML frontmatter."""
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    return TranscriptDoc(
        path=path,
        body=body,
        source=meta.get("source") or meta.get("creator") or meta.get("channel"),
        title=meta.get("title"),
        url=meta.get("url"),
        date=meta.get("date"),
    )


def discover_transcripts(directory: Path) -> list[TranscriptDoc]:
    """Return transcript docs sorted by filename (stable, deterministic)."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Transcripts directory not found: {directory}")
    paths = sorted(
        p
        for p in directory.iterdir()
        if p.is_file()
        and p.suffix.lower() in TRANSCRIPT_SUFFIXES
        and not p.name.lower().startswith("readme")
    )
    return [load_transcript(p) for p in paths]


def build_condense_user_prompt(
    transcripts: list[TranscriptDoc],
    *,
    existing_principles: str | None = None,
    max_chars_per_transcript: int = 24_000,
) -> str:
    """Build the user prompt that asks the LLM to draft expert-principles.md."""
    if not transcripts:
        raise ValueError("No transcripts to condense")

    parts: list[str] = [
        "Synthesize the following transcripts into expert-principles.md.",
        f"Hard size budget: {TARGET_EXPERT_PRINCIPLES_CHARS} characters.",
    ]
    if existing_principles and existing_principles.strip():
        parts.append(
            "Merge with / replace overlapping content from the EXISTING draft "
            "below; keep useful bullets that are not contradicted.\n\n"
            f"EXISTING DRAFT:\n{existing_principles.strip()}\n"
        )

    for i, doc in enumerate(transcripts, start=1):
        header = f"TRANSCRIPT {i}: {doc.label()}"
        extras = []
        if doc.url:
            extras.append(f"url={doc.url}")
        if doc.date:
            extras.append(f"date={doc.date}")
        if extras:
            header = f"{header} ({', '.join(extras)})"
        body = doc.body
        if len(body) > max_chars_per_transcript:
            body = (
                body[: max_chars_per_transcript - 40].rstrip()
                + "\n\n[truncated for condensation prompt]"
            )
        parts.append(f"{header}\n\n{body}")

    return "\n\n====\n\n".join(parts)


LLMClient = Callable[[str, str], str]


def condense_transcripts(
    transcripts: list[TranscriptDoc],
    *,
    llm: LLMClient,
    existing_principles: str | None = None,
) -> str:
    """Call ``llm(system, user)`` and return draft expert-principles markdown."""
    prompt = build_condense_user_prompt(
        transcripts, existing_principles=existing_principles
    )
    draft = llm(CONDENSE_SYSTEM, prompt).strip()
    if not draft.startswith("#"):
        draft = f"# Expert Training Principles\n\n{draft}"
    if len(draft) > TARGET_EXPERT_PRINCIPLES_CHARS:
        logger.warning(
            "Condensed principles exceed budget (%d > %d); truncating for safety",
            len(draft),
            TARGET_EXPERT_PRINCIPLES_CHARS,
        )
        draft = (
            draft[: TARGET_EXPERT_PRINCIPLES_CHARS - 40].rstrip()
            + "\n\n[truncated — tighten before upload]"
        )
    return draft


def skeleton_expert_principles() -> str:
    """Return the curated starter skeleton (also used as fixture baseline)."""
    return (
        "# Expert Training Principles\n"
        "\n"
        "Human-reviewed bullets distilled from trusted coaches and researchers.\n"
        "The briefing and coaching chat cite this file — they do not invent principles.\n"
        "\n"
        "## Volume landmarks (RP / Israetel-style)\n"
        "\n"
        "- Most lifters grow best with **~10–20 hard sets per muscle per week**, "
        "split across 2+ sessions (MEV near the low end; MRV is individual).\n"
        "- When sleep or HRV is suppressed for several days, **reduce volume "
        "before intensity**.\n"
        "- Deloads are planned reductions (~40–50% volume), not random off weeks; "
        "keep some intensity so skill does not decay.\n"
        "\n"
        "## Progressive overload & proximity to failure (Nippard / Ethier-style)\n"
        "\n"
        "- Progress load, reps, or quality every **1–2 weeks** on key compounds "
        "when recovery allows.\n"
        "- Stop a set **1–2 reps shy of form breakdown** for most working sets; "
        "reserve true failure for safer isolation work.\n"
        "- Prioritize **full ROM with control** over load chasing on compounds.\n"
        "\n"
        "## Recovery\n"
        "\n"
        "- Sleep is the highest-leverage recovery tool; treat chronic short sleep "
        "as a programming constraint, not a willpower gap.\n"
        "- HRV well below personal baseline for multiple days → bias toward "
        "easier sessions or volume cuts.\n"
        "\n"
        "## Cardio & health\n"
        "\n"
        "- Keep **easy days truly easy**; hard intervals and long runs need "
        "recovery between.\n"
        "- Increase weekly mileage gradually; large week-over-week jumps raise "
        "injury risk (~10% rule as a soft ceiling, not a law).\n"
        "- Zone 2 / conversational aerobic work supports metabolic and "
        "cardiovascular health alongside lifting.\n"
        "\n"
        "## Injury prevention\n"
        "\n"
        "- Rotate or substitute exercises when a joint trend flares; don't grind "
        "through sharp pain.\n"
        "- Avoid training the same movement pattern to failure on consecutive days.\n"
        "\n"
        "## How this file is built\n"
        "\n"
        "Paste owned/official transcripts into `tmp/guidelines-transcripts/`, "
        "then run `scripts/condense_expert_principles.py` (or synthesize in "
        "Cursor). Human-review before `make guidelines-sync`. "
        "See `scripts/guidelines-corpus.md`.\n"
    )

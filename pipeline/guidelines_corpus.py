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
    """Return the curated expert-principles corpus (fixture baseline).

    Distilled from operator-supplied RP / Israetel transcripts — keep under
    ``TARGET_EXPERT_PRINCIPLES_CHARS`` for prompt injection.
    """
    return (
        "# Expert Training Principles\n"
        "\n"
        "Human-reviewed bullets distilled from RP Strength / Dr. Mike Israetel videos\n"
        "(time-efficient lifting, cardio-by-goal, minimal-equipment intensity). The\n"
        "briefing and coaching chat cite this file — they do not invent principles.\n"
        "\n"
        "## Time-efficient strength (RP minimalist templates)\n"
        "\n"
        "- Prefer **antagonist supersets / tri-sets** with **~5–10 s** between exercises\n"
        "  (up to ~30 s if form slips) so short sessions still hit volume + some cardio.\n"
        "- Session templates that work: **push+pull**, **squat+hinge**, then an\n"
        "  **isolation pair**; or a 4-day upper/lower with 2–3 pairings per day.\n"
        "- Dose: **2–3×/week full-body** (~20–40 min) or **4–5×/week** alternating\n"
        "  upper/lower (~30 min). Start at **1 set per exercise**, build to **2–3**\n"
        "  (up to ~5 if time/recovery allow).\n"
        "- Target **~10–20 reps** (first set often **15–20**); keep most sets at\n"
        "  **~1–2 RIR**, or true failure on safe home/isolation work when intensity is\n"
        "  the limiter.\n"
        "- Progress: **+1 rep/week for 1–2 months**, then bump load or harden leverage;\n"
        "  swap exercises freely if pairings stay non-overlapping.\n"
        "- Always **full ROM**, controlled eccentric, powerful concentric — never\n"
        "  sacrifice technique for rest length; rest longer instead.\n"
        "\n"
        "## Cardio by goal (RP / Israetel)\n"
        "\n"
        "Match dose to the goal; intensity is a **technical** choice, not punishment.\n"
        "\n"
        "- **Strength/power focus:** favor easy modalities (walk, swim, elliptical);\n"
        "  keep most work **under ~140 bpm** and **under ~30 min**. **~8k daily steps**\n"
        "  is often enough in a hard lifting block; more dedicated cardio can cost\n"
        "  strength expression.\n"
        "- **Muscularity / hypertrophy:** **~8–12k steps**; extra cardio usually\n"
        "  **under ~140 bpm** and **under ~60 min**, **3–7×/week**. Long hard sessions\n"
        "  (~160–180 bpm × 60 min) commonly add fatigue that **slows muscle gains**.\n"
        "- **Health (alongside lifting):** steps help (**~10k**), but walking alone is\n"
        "  not enough for best health adaptations — include at least **~3×/week ×\n"
        "  ~15 min** where HR is **~150+** (talking gets hard). A strong trade-off for\n"
        "  many is **~4–6×/week × 30–45 min** at **150+ bpm**, if sleep/strength hold.\n"
        "- **Leanness:** **~10k steps** can drive fat loss if intake is controlled;\n"
        "  more cardio only if recovery and **strength (muscle proxy)** stay stable.\n"
        "  Hard **150+ bpm** work **3–6×/week × 30–60 min** can accelerate leanness,\n"
        "  especially if leg size is not a priority.\n"
        "- **Endurance focus:** sport-specific; wave easy/moderate/hard days; rotate\n"
        "  modalities to spare joints; get a dedicated endurance coach/app beyond\n"
        "  hobby doses.\n"
        "\n"
        "## Cardio practices & pitfalls\n"
        "\n"
        "- Choose modalities you **enjoy**, can **repeat**, and that **joints tolerate**;\n"
        "  switch (bike/elliptical/swim) when one pattern irritates tissue.\n"
        "- **Ramp gradually:** start ~**3×/week × ~15 min** just out of breath — jumping\n"
        "  to high volume/running invites shin splints and burnout.\n"
        "- **Track steps** so NEAT does not silently drop in a deficit.\n"
        "- Excess cardio usually **slows gains before it erases them**; if size/strength\n"
        "  stall after a cardio increase, back off. Do not use cardio as self-punishment.\n"
        "\n"
        "## Minimal equipment / travel\n"
        "\n"
        "- Scale difficulty via **leverage and ROM** (foot position, bar height,\n"
        "  regressions) so every set still lands in a productive rep range.\n"
        "- Short rests condense time and raise heart rate; hypertrophy still needs\n"
        "  **near-limit effort** on each set.\n"
        "- Bias weekly sets toward lagging muscle groups; keep a separate lower-body\n"
        "  plan — upper-only circuits do not replace legs.\n"
        "\n"
        "## Sources\n"
        "\n"
        "- RP Strength — time-efficient / dumbbell templates "
        "(`Q-xQX79woHI`, `p8fBbQSDKQY`)\n"
        "- Dr. Mike Israetel / RP — cardio by goal 2026 (`0r_AlKrc70c`)\n"
        "- Dr. Mike + Jared Feather / RP — at-home upper minimal equipment "
        "(`HjWZ-kKJXgc`)\n"
    )

#!/usr/bin/env python3.14
"""Condense manual YouTube transcripts into expert-principles.md.

Operator flow (ToS-safe — no scrapers):

1. Drop transcript ``.md`` / ``.txt`` files into ``tmp/guidelines-transcripts/``
   (optional YAML frontmatter: source, title, url, date).
2. Print a prompt for Cursor/Claude, **or** call Anthropic with ``--llm``.
3. Human-review the draft, copy into
   ``tmp/soma_guidelines/guidelines/<user_id>/expert-principles.md``.
4. ``make guidelines-sync`` to upload to S3.

Examples (from repo root, venv active)::

  # Prompt only (paste into Cursor — recommended for review):
  .venv/bin/python scripts/condense_expert_principles.py --print-prompt

  # Draft via Anthropic API:
  ANTHROPIC_API_KEY=... .venv/bin/python scripts/condense_expert_principles.py \\
    --llm --output tmp/soma_guidelines/guidelines/$SOMA_USER_ID/expert-principles.md

  # Write the starter skeleton (no transcripts required):
  .venv/bin/python scripts/condense_expert_principles.py --skeleton \\
    --output tmp/soma_guidelines/guidelines/$SOMA_USER_ID/expert-principles.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow ``python scripts/condense_expert_principles.py`` without install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.briefing import DEFAULT_BRIEFING_MODEL  # noqa: E402
from pipeline.guidelines_corpus import (  # noqa: E402
    CONDENSE_SYSTEM,
    build_condense_user_prompt,
    condense_transcripts,
    discover_transcripts,
    skeleton_expert_principles,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--transcripts-dir",
        type=Path,
        default=Path("tmp/guidelines-transcripts"),
        help="Directory of manually pasted transcript .md/.txt files",
    )
    p.add_argument(
        "--existing",
        type=Path,
        default=None,
        help="Optional existing expert-principles.md to merge against",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write draft markdown here (directories created as needed)",
    )
    p.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print system + user prompt to stdout (no API call)",
    )
    p.add_argument(
        "--llm",
        action="store_true",
        help="Call Anthropic (requires ANTHROPIC_API_KEY) and write draft",
    )
    p.add_argument(
        "--skeleton",
        action="store_true",
        help="Write the curated starter skeleton (ignores transcripts)",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("BRIEFING_MODEL", DEFAULT_BRIEFING_MODEL),
        help="Anthropic model id when using --llm",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.skeleton:
        draft = skeleton_expert_principles()
        return _emit(draft, args.output)

    transcripts = discover_transcripts(args.transcripts_dir)
    if not transcripts:
        print(
            f"No transcript files in {args.transcripts_dir}. "
            "Add .md/.txt files (see scripts/guidelines-corpus.md).",
            file=sys.stderr,
        )
        return 1

    existing = None
    if args.existing is not None:
        existing = args.existing.read_text(encoding="utf-8")

    if args.print_prompt or not args.llm:
        user = build_condense_user_prompt(transcripts, existing_principles=existing)
        print("=== SYSTEM ===\n")
        print(CONDENSE_SYSTEM)
        print("\n=== USER ===\n")
        print(user)
        if not args.llm:
            print(
                "\n# Tip: re-run with --llm to call Anthropic, "
                "or paste the prompt above into Cursor.",
                file=sys.stderr,
            )
            return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY is required for --llm", file=sys.stderr)
        return 1

    from pipeline.clients import anthropic_llm

    llm = anthropic_llm(api_key, model=args.model, max_tokens=4096)
    draft = condense_transcripts(
        transcripts, llm=llm, existing_principles=existing
    )
    return _emit(draft, args.output)


def _emit(draft: str, output: Path | None) -> int:
    if output is None:
        print(draft)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(draft if draft.endswith("\n") else draft + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(draft)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

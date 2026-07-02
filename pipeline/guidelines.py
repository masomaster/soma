"""Phase 10 personal guidelines corpus (S3 or local disk).

Loads ``my-goals.md``, ``injury-history.md``, and ``expert-principles.md`` per
user and injects bounded text into briefing / coaching prompts. Chat may append
narrative notes to ``my-goals.md`` via the same storage path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GUIDELINE_FILES = ("my-goals.md", "injury-history.md", "expert-principles.md")
DEFAULT_MAX_CHARS = 4000

# Canonical S3 key: guidelines/{user_id}/{filename}
def guideline_object_key(user_id: str, filename: str) -> str:
    """Return the object key for one guideline markdown file."""
    if filename not in GUIDELINE_FILES:
        raise ValueError(f"Unknown guideline file: {filename!r}")
    return f"guidelines/{user_id}/{filename}"


@dataclass(frozen=True, slots=True)
class GuidelinesContext:
    """Bounded guideline text for prompt injection."""

    my_goals: str | None = None
    injury_history: str | None = None
    expert_principles: str | None = None

    def has_content(self) -> bool:
        return bool(self.my_goals or self.injury_history or self.expert_principles)


def _truncate(text: str, *, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 24].rstrip() + "\n\n[truncated for prompt]"


def format_guidelines_for_prompt(ctx: GuidelinesContext | None) -> str:
    """Render guideline blocks for LLM prompts."""
    if ctx is None or not ctx.has_content():
        return ""
    parts: list[str] = []
    if ctx.my_goals:
        parts.append(f"PERSONAL GOALS (my-goals.md):\n{ctx.my_goals}")
    if ctx.injury_history:
        parts.append(
            "INJURY HISTORY (injury-history.md — respect constraints; do not invent injuries):\n"
            f"{ctx.injury_history}"
        )
    if ctx.expert_principles:
        parts.append(f"EXPERT PRINCIPLES (expert-principles.md):\n{ctx.expert_principles}")
    return "\n\n".join(parts) + "\n\n"


# get_object(key) -> bytes | None
ObjectGetter = Callable[[str], bytes | None]
# put_object(key, body: bytes) -> None
ObjectPutter = Callable[[str, bytes], None]


def load_guidelines(
    user_id: str,
    *,
    get_object: ObjectGetter,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> GuidelinesContext:
    """Load all guideline files for a user; missing files are omitted."""
    loaded: dict[str, str | None] = {}
    for filename in GUIDELINE_FILES:
        key = guideline_object_key(user_id, filename)
        raw = get_object(key)
        if raw is None:
            loaded[filename] = None
            continue
        loaded[filename] = _truncate(raw.decode("utf-8"), max_chars=max_chars)
    return GuidelinesContext(
        my_goals=loaded["my-goals.md"],
        injury_history=loaded["injury-history.md"],
        expert_principles=loaded["expert-principles.md"],
    )


def append_goal_note(
    user_id: str,
    text: str,
    *,
    get_object: ObjectGetter,
    put_object: ObjectPutter,
) -> str:
    """Append a timestamped note to ``my-goals.md``."""
    key = guideline_object_key(user_id, "my-goals.md")
    existing = get_object(key)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"\n\n## Note ({stamp})\n{text.strip()}\n"
    if existing is None:
        new_body = f"# Personal goals\n{body}"
    else:
        new_body = existing.decode("utf-8").rstrip() + body
    put_object(key, new_body.encode("utf-8"))
    return "Appended note to my-goals.md"


def local_guidelines_storage(base_dir: str | Path) -> tuple[ObjectGetter, ObjectPutter]:
    """Filesystem-backed guideline storage for local dev."""
    root = Path(base_dir)

    def get_object(key: str) -> bytes | None:
        path = root / key
        if not path.is_file():
            return None
        return path.read_bytes()

    def put_object(key: str, body: bytes) -> None:
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    return get_object, put_object


def resolve_guidelines_storage() -> tuple[ObjectGetter, ObjectPutter] | None:
    """Pick local disk or S3 from environment; return None if unconfigured."""
    local_dir = os.environ.get("SOMA_GUIDELINES_LOCAL_DIR", "").strip()
    if local_dir:
        return local_guidelines_storage(local_dir)

    bucket = os.environ.get("SOMA_GUIDELINES_BUCKET", "").strip()
    if bucket:
        return s3_guidelines_storage(bucket)

    return None


def s3_guidelines_storage(bucket: str) -> tuple[ObjectGetter, ObjectPutter]:
    """S3-backed guideline storage (Lambda / AWS)."""

    def get_object(key: str) -> bytes | None:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("s3")
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        body = resp.get("Body")
        if body is None:
            return None
        return body.read()

    def put_object(key: str, body: bytes) -> None:
        import boto3

        client = boto3.client("s3")
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/markdown")

    return get_object, put_object


def load_guidelines_from_env(user_id: str) -> GuidelinesContext | None:
    """Convenience loader using ``SOMA_GUIDELINES_*`` env vars."""
    storage = resolve_guidelines_storage()
    if storage is None:
        return None
    get_object, _ = storage
    ctx = load_guidelines(user_id, get_object=get_object)
    return ctx if ctx.has_content() else None

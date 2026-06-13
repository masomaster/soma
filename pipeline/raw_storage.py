"""S3 raw object key layout (raw JSON before normalization)."""

from __future__ import annotations

from datetime import datetime, timezone


def format_raw_object_key(user_id: str, source: str, at: datetime) -> str:
    """Return ``raw/{user_id}/{source}/{YYYY-MM-DD}/{HHMMSS_micro}.json`` (UTC).

    Matches ``.cursor/rules/soma.mdc`` raw path convention.
    """
    if at.tzinfo is None:
        at_utc = at.replace(tzinfo=timezone.utc)
    else:
        at_utc = at.astimezone(timezone.utc)
    day = at_utc.strftime("%Y-%m-%d")
    stamp = at_utc.strftime("%H%M%S_%f")
    return f"raw/{user_id}/{source}/{day}/{stamp}.json"

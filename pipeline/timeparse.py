"""Shared timestamp parsing / UTC normalization for adapters and dedup.

Consolidates the ISO-8601-ish parsing used across HAE workouts, Strava, and the
cardio dedup so the ``Z``-suffix, space-vs-``T`` separator, and naive→UTC handling
live in one place.
"""

from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime; leave timezone-aware datetimes unchanged."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_iso_datetime_utc(raw: object) -> datetime | None:
    """Parse an ISO-8601-ish timestamp to a timezone-aware datetime (UTC if naive).

    Accepts ``T`` or space separators and a ``Z`` suffix — e.g. Strava
    ``"2024-06-01T12:34:56Z"`` and HAE ``"2024-06-01 07:30:00 +0000"``. Returns
    ``None`` for non-strings or values with no parseable time component (e.g. a
    bare date), so callers keep the field NULL rather than inventing midnight.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return ensure_utc(datetime.fromisoformat(candidate.replace("Z", "+00:00")))
        except ValueError:
            continue
    try:
        return ensure_utc(datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z"))
    except ValueError:
        return None

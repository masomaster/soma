"""Source-aware near-duplicate dedup for Apple Health hub ``cardio_events``.

The same session often lands in Apple Health up to three times: **Nike Run Club**
logs the run, mirrors it to **Strava**, and **Fitbit/Google** copies it in via
**Health Sync** — plus native Watch/phone can add its own. All share
``source = 'apple_health'`` and different HealthKit UUIDs, so
``ON CONFLICT (user_id, source_id)`` does not catch them.

**Matching** (see :func:`near_duplicate_apple_cardio`): same calendar day + same
*activity family* (run/walk/ride/…) + **start times within ±15 min** when both rows
carry ``started_at``. Start-time proximity is deliberate — Fitbit's duration and
distance are unreliable, so the legacy duration/distance tolerance let its
duplicates slip through. When ``started_at`` is missing (pre-0006 rows) we fall back
to the legacy duration ±5 min / distance ±0.15 mi rule.

**Resolution** (see :data:`pipeline.source_priority.CARDIO_SOURCE_APP_PRIORITY`):
within a duplicate cluster keep the **highest-priority source app** — NRC > Strava >
native Apple Watch/iPhone > Fitbit/Google — breaking ties by field richness. A
Fitbit workout with no higher-priority overlap is kept (e.g. a walk logged only by
Fitbit). Incoming rows that outrank an already-stored duplicate **supersede** it:
the lower-priority stored row's ``source_id`` is returned for deletion.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from pipeline.cardio_quality import assess_cardio_quality, has_suspect_distance
from pipeline.features import as_date
from pipeline.source_priority import cardio_source_app_rank
from pipeline.timeparse import ensure_utc, parse_iso_datetime_utc

logger = logging.getLogger(__name__)

_DURATION_TOL_MIN = 5.0
_DISTANCE_TOL_MI = 0.15
# Same session's start time can drift across apps (Fitbit clock, sync lag); two
# genuinely distinct sessions rarely start this close in the same activity family.
# Kept deliberately tight because a superseded match triggers a hard DELETE.
_START_TOL_MIN = 15.0

# (token-prefix, family). First match wins; matched against whole word tokens so
# "Throwing" does not collapse to "row" and "Running" resolves to "run".
_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("run", "run"),
    ("jog", "run"),
    ("walk", "walk"),
    ("hik", "hike"),
    ("cycl", "ride"),
    ("bike", "ride"),
    ("ride", "ride"),
    ("spin", "ride"),
    ("swim", "swim"),
    ("strength", "strength"),
    ("weight", "strength"),
    ("elliptical", "elliptical"),
    ("row", "row"),
)


def _normalize_activity_type(raw: Any) -> str:
    if not isinstance(raw, str):
        return "workout"
    return " ".join(raw.strip().lower().split())


def activity_family(raw: Any) -> str:
    """Coarse activity family so cross-app names (``Outdoor Run`` vs ``Running``) match.

    Matches keyword prefixes against whole word tokens (not raw substrings), so
    unrelated names like ``Throwing`` are not mis-bucketed. Unknown names return
    their normalized form, so custom activities only match identical names.
    """
    s = _normalize_activity_type(raw)
    if not s or s == "workout":
        return "workout"
    tokens = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    for keyword, family in _FAMILY_KEYWORDS:
        if any(token.startswith(keyword) for token in tokens):
            return family
    return s


def _start_dt(row: dict[str, Any]) -> datetime | None:
    v = row.get("started_at")
    if isinstance(v, datetime):
        return ensure_utc(v)
    return parse_iso_datetime_utc(v)


def _row_richness(row: dict[str, Any]) -> int:
    score = 0
    if row.get("distance_miles") is not None:
        score += 2
    if row.get("avg_hr") is not None:
        score += 1
    if row.get("max_hr") is not None:
        score += 1
    if row.get("calories") is not None:
        score += 1
    if row.get("elevation_ft") is not None:
        score += 1
    return score


def _distance_is_trustworthy(row: dict[str, Any]) -> bool:
    """False when the row is a run whose pace marks its distance as untrustworthy.

    Non-runs (and runs without a computable pace) are always trustworthy here —
    this only demotes runs flagged by :func:`assess_cardio_quality` (implausibly
    fast *or* slow pace), which is exactly the corrupt-track case dedup must avoid
    keeping over a clean duplicate of the same session.
    """
    return not has_suspect_distance(assess_cardio_quality(row))


def _resolution_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    """Sort key (descending) for which row wins a duplicate cluster.

    Ordered by source-app priority, then whether the recorded distance is
    trustworthy (a clean copy beats a glitched one of the same session), then
    field richness, then ``source_id`` as a stable, deterministic final tiebreak.
    Plausibility sits above richness so a corrupt-distance row can never win a
    cluster on field count alone.
    """
    return (
        cardio_source_app_rank(row.get("source_app")),
        1 if _distance_is_trustworthy(row) else 0,
        _row_richness(row),
        str(row.get("source_id") or ""),
    )


def _legacy_duration_distance_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Pre-0006 fallback when ``started_at`` is absent: duration/distance tolerance."""
    try:
        dur_a = float(a.get("duration_min") or 0)
        dur_b = float(b.get("duration_min") or 0)
    except (TypeError, ValueError):
        return False
    if abs(dur_a - dur_b) > _DURATION_TOL_MIN:
        return False
    dist_a = a.get("distance_miles")
    dist_b = b.get("distance_miles")
    if dist_a is not None and dist_b is not None:
        try:
            if abs(float(dist_a) - float(dist_b)) > _DISTANCE_TOL_MI:
                return False
        except (TypeError, ValueError):
            return False
    return True


def near_duplicate_apple_cardio(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two ``apple_health`` rows likely describe the same session."""
    if a.get("source") != "apple_health" or b.get("source") != "apple_health":
        return False
    da = as_date(a.get("event_date"))
    db = as_date(b.get("event_date"))
    if da is None or da != db:
        return False
    if activity_family(a.get("activity_type")) != activity_family(b.get("activity_type")):
        return False
    start_a = _start_dt(a)
    start_b = _start_dt(b)
    if start_a is not None and start_b is not None:
        return abs((start_a - start_b).total_seconds()) <= _START_TOL_MIN * 60.0
    # Fallback for rows without a start timestamp (pre-0006 / non-HAE).
    return _legacy_duration_distance_match(a, b)


def _dedupe_batch(cardio_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the highest-priority (then richest) row per near-duplicate cluster.

    Rows are bucketed by ``(event_date, activity_family)`` first: a near-duplicate
    requires both to match, so rows in different buckets can never collide. This
    keeps the per-bucket O(n²) comparison small even when called over a large
    history (e.g. the backfill), rather than comparing every row to every other.
    """
    if len(cardio_rows) < 2:
        return cardio_rows, 0
    buckets: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for row in cardio_rows:
        key = (as_date(row.get("event_date")), activity_family(row.get("activity_type")))
        buckets.setdefault(key, []).append(row)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for bucket_rows in buckets.values():
        bucket_kept: list[dict[str, Any]] = []
        for row in sorted(bucket_rows, key=_resolution_key, reverse=True):
            if any(near_duplicate_apple_cardio(row, k) for k in bucket_kept):
                dropped += 1
                continue
            bucket_kept.append(row)
        kept.extend(bucket_kept)
    return kept, dropped


def apple_cardio_rows_to_drop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the losing rows within near-duplicate clusters (keep highest-priority/richest).

    Uses the same rules as ingest-time dedup. Safe for retroactive DB cleanup —
    callers delete by ``source_id`` or row ``id``.
    """
    kept, dropped = _dedupe_batch(rows)
    if dropped == 0:
        return []
    kept_ids = {id(r) for r in kept}
    return [r for r in rows if id(r) not in kept_ids]


def _load_existing_apple_cardio(
    cur: Any,
    *,
    user_id: str,
    dates: list[date],
) -> list[dict[str, Any]]:
    if not dates:
        return []
    cur.execute(
        """
        SELECT source, source_id, event_date, activity_type, duration_min,
               distance_miles, elevation_ft, avg_hr, max_hr, avg_pace_sec_mi,
               calories, effort_zone, session_rpe, notes, started_at, source_app
        FROM cardio_events
        WHERE user_id = %s::uuid
          AND source = %s
          AND event_date = ANY(%s::date[])
        """,
        (user_id, "apple_health", dates),
    )
    cols = (
        "source",
        "source_id",
        "event_date",
        "activity_type",
        "duration_min",
        "distance_miles",
        "elevation_ft",
        "avg_hr",
        "max_hr",
        "avg_pace_sec_mi",
        "calories",
        "effort_zone",
        "session_rpe",
        "notes",
        "started_at",
        "source_app",
    )
    out: list[dict[str, Any]] = []
    for row in cur.fetchall():
        out.append(dict(zip(cols, row, strict=True)))
    return out


def filter_near_duplicate_apple_cardio(
    cur: Any,
    *,
    user_id: str,
    cardio_rows: list[dict[str, Any]],
    allow_supersede: bool = True,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Resolve near-duplicate ``apple_health`` cardio before upsert.

    Returns ``(filtered_rows, dropped_count, superseded_source_ids)`` where:

    * ``filtered_rows`` are the rows to insert (highest-priority per cluster),
    * ``dropped_count`` is how many incoming rows were skipped as duplicates,
    * ``superseded_source_ids`` are **already-stored** rows that a higher-priority
      incoming row replaces — the caller should delete them before upsert.

    ``allow_supersede`` gates the **destructive** cross-POST behavior: when False
    (e.g. an unauthenticated webhook), a higher-priority incoming row does *not*
    delete a stored duplicate — the incoming row is dropped instead, so the path
    stays insert-only. Callers that have authenticated the tenant pass True.
    """
    if not cardio_rows:
        return cardio_rows, 0, []

    batch_kept, batch_dropped = _dedupe_batch(cardio_rows)
    if not batch_kept:
        return batch_kept, batch_dropped, []

    day_set: set[date] = set()
    for r in batch_kept:
        d = as_date(r.get("event_date"))
        if d is not None:
            day_set.add(d)
    existing = _load_existing_apple_cardio(cur, user_id=user_id, dates=sorted(day_set))

    final: list[dict[str, Any]] = []
    db_dropped = 0
    superseded_ids: list[str] = []
    superseded_seen: set[str] = set()

    for row in batch_kept:
        matches = [ex for ex in existing if near_duplicate_apple_cardio(row, ex)]
        if not matches:
            final.append(row)
            continue
        row_key = _resolution_key(row)
        # An existing row that ranks equal-or-higher on the full resolution key
        # (source priority, distance trust, richness, id) already covers this
        # session. When supersede is disabled, treat *any* stored duplicate as
        # blocking so the path never deletes — the stored row wins and the
        # incoming is dropped.
        if not allow_supersede or any(_resolution_key(ex) >= row_key for ex in matches):
            db_dropped += 1
            continue
        # Incoming outranks every stored duplicate — supersede the lower-ranked rows.
        for ex in matches:
            sid = ex.get("source_id")
            if isinstance(sid, str) and sid and sid not in superseded_seen:
                superseded_seen.add(sid)
                superseded_ids.append(sid)
        final.append(row)

    total_dropped = batch_dropped + db_dropped
    if total_dropped or superseded_ids:
        logger.info(
            "Apple Health hub dedup: dropped %d incoming near-dup(s) "
            "(batch=%d, existing_wins=%d), superseding %d stored row(s) for user %s",
            total_dropped,
            batch_dropped,
            db_dropped,
            len(superseded_ids),
            user_id,
        )
    return final, total_dropped, superseded_ids

"""Cardio row plausibility checks (field-level trust, not row-level).

A single corrupt field must never discard a session. The canonical case: a run
lands with real duration/HR/calories but a broken ``distance_miles`` (GPS dropout,
partial track, indoor segment), yielding an impossible pace. Rather than drop the
row or trust the distance, we tag it: the session still counts for frequency and
duration, but the suspect distance is excluded from mileage/pace aggregates and
surfaced for the athlete to verify.

Pure and dependency-free (no DB / network / thresholds IO): callers pass a row
dict plus optional pace bands. Bands default to the module constants and mirror
``rules.DEFAULT_THRESHOLDS`` (``cardio_run_pace_{min,max}_sec_mi``) so operators can
override per user in SSM and have the daily pipeline re-tag from the same numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# HealthKit / HAE workout names that usually mirror Hevy strength sessions.
STRENGTH_LIKE_CARDIO_ACTIVITIES: frozenset[str] = frozenset(
    {
        "traditional strength training",
        "functional strength training",
        "core training",
    }
)


def is_strength_like_cardio_activity(activity_type: Any) -> bool:
    """True when ``activity_type`` is a strength-style workout mirrored into cardio."""
    if not isinstance(activity_type, str):
        return False
    return activity_type.strip().lower() in STRENGTH_LIKE_CARDIO_ACTIVITIES


CYCLING_KEYWORDS = ("cycl", "bike", "ride", "spin")


def cardio_mode(activity_type: Any) -> str:
    """Bucket a cardio ``activity_type`` into ``running`` / ``cycling`` / ``other``."""
    a = str(activity_type or "").lower()
    if "run" in a:
        return "running"
    if any(k in a for k in CYCLING_KEYWORDS):
        return "cycling"
    return "other"


# Plausible run pace band, seconds per mile. Below the floor is faster than an
# elite miler (data glitch); above the ceiling is slower than a brisk walk, so a
# "run" that slow almost always means the distance was under-recorded.
DEFAULT_RUN_PACE_MIN_SEC_MI = 210.0  # 3:30 / mi
DEFAULT_RUN_PACE_MAX_SEC_MI = 1080.0  # 18:00 / mi

# quality_flags token: the recorded distance (hence pace) is not trustworthy.
FLAG_IMPLAUSIBLE_RUN_PACE = "implausible_run_pace"
# FIT/TCX/GPX session had no power samples (still useful as cardio duration).
FLAG_NO_POWER = "no_power"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_run(activity_type: Any) -> bool:
    """Run detection — same convention as :func:`cardio_mode`."""
    return cardio_mode(activity_type) == "running"


def run_pace_sec_per_mile(row: Mapping[str, Any]) -> float | None:
    """Recompute a run's pace (sec/mi) from ``duration_min`` / ``distance_miles``.

    Returns ``None`` for non-runs or when distance/duration are missing or
    non-positive. The recomputed pace is authoritative (a stored
    ``avg_pace_sec_mi`` may itself be derived from a bad distance).
    """
    if not _is_run(row.get("activity_type")):
        return None
    distance = _num(row.get("distance_miles"))
    duration = _num(row.get("duration_min"))
    if distance is None or distance <= 0 or duration is None or duration <= 0:
        return None
    return duration * 60.0 / distance


def assess_cardio_quality(
    row: Mapping[str, Any],
    *,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
    run_pace_max_sec_mi: float = DEFAULT_RUN_PACE_MAX_SEC_MI,
) -> list[str]:
    """Return quality flag tokens for one cardio row (empty when it looks clean).

    Only runs with a positive distance and duration are checked; the pace is
    recomputed from ``duration_min`` / ``distance_miles`` (authoritative) rather
    than trusting a stored ``avg_pace_sec_mi``. A pace outside the band flags the
    row via :data:`FLAG_IMPLAUSIBLE_RUN_PACE` so the athlete can verify it.
    """
    flags: list[str] = []
    pace_sec_mi = run_pace_sec_per_mile(row)
    if pace_sec_mi is not None and (
        pace_sec_mi < run_pace_min_sec_mi or pace_sec_mi > run_pace_max_sec_mi
    ):
        flags.append(FLAG_IMPLAUSIBLE_RUN_PACE)
    return flags


def has_suspect_distance(quality_flags: Sequence[str] | None) -> bool:
    """True when a row's flags mark its distance/pace as untrustworthy."""
    return bool(quality_flags) and FLAG_IMPLAUSIBLE_RUN_PACE in quality_flags


def is_overrecorded_distance(
    row: Mapping[str, Any],
    *,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
) -> bool:
    """True when a run's pace is implausibly *fast* — distance over-recorded.

    A pace below the floor (faster than an elite miler) means the recorded
    distance is almost certainly inflated (e.g. GPS multiplication / a duplicated
    track), so it must be excluded from mileage totals.

    A pace *above* the ceiling (slower than a brisk walk) is deliberately **not**
    treated as over-recorded: it reflects a real distance covered with walk
    breaks or a paused/inflated timer, so the distance still counts toward weekly
    mileage. It is still surfaced as a "worth verifying" data-quality note via
    :func:`assess_cardio_quality` / :func:`has_suspect_distance`.
    """
    pace_sec_mi = run_pace_sec_per_mile(row)
    return pace_sec_mi is not None and pace_sec_mi < run_pace_min_sec_mi

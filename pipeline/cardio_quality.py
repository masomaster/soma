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

# Plausible run pace band, seconds per mile. Below the floor is faster than an
# elite miler (data glitch); above the ceiling is slower than a brisk walk, so a
# "run" that slow almost always means the distance was under-recorded.
DEFAULT_RUN_PACE_MIN_SEC_MI = 210.0  # 3:30 / mi
DEFAULT_RUN_PACE_MAX_SEC_MI = 1080.0  # 18:00 / mi

# quality_flags token: the recorded distance (hence pace) is not trustworthy.
FLAG_IMPLAUSIBLE_RUN_PACE = "implausible_run_pace"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_run(activity_type: Any) -> bool:
    """Run detection: ``"run"`` substring, matching the convention used across
    goal_progress / mileage_ramp / metrics_summary."""
    return "run" in str(activity_type or "").lower()


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
    distance as suspect via :data:`FLAG_IMPLAUSIBLE_RUN_PACE`.
    """
    flags: list[str] = []
    if _is_run(row.get("activity_type")):
        distance = _num(row.get("distance_miles"))
        duration = _num(row.get("duration_min"))
        if distance is not None and distance > 0 and duration is not None and duration > 0:
            pace_sec_mi = duration * 60.0 / distance
            if pace_sec_mi < run_pace_min_sec_mi or pace_sec_mi > run_pace_max_sec_mi:
                flags.append(FLAG_IMPLAUSIBLE_RUN_PACE)
    return flags


def has_suspect_distance(quality_flags: Sequence[str] | None) -> bool:
    """True when a row's flags mark its distance/pace as untrustworthy."""
    return bool(quality_flags) and FLAG_IMPLAUSIBLE_RUN_PACE in quality_flags

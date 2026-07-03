"""Weekly running mileage change detection (Slice A).

Compares ISO-week running distance to the prior week and flags abrupt
increases that may warrant a ramp-down caution in briefing / dashboard.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.cardio_quality import (
    DEFAULT_RUN_PACE_MAX_SEC_MI,
    DEFAULT_RUN_PACE_MIN_SEC_MI,
    is_overrecorded_distance,
)
from pipeline.units import KM_PER_MILE

# Default max week-over-week increase before flagging (percent).
DEFAULT_MAX_WEEKLY_INCREASE_PCT = 15.0


def iso_week_start(d: date) -> date:
    """Return Monday of the ISO week containing ``d``."""
    return d - timedelta(days=d.weekday())


def _km_from_row(row: Mapping[str, Any]) -> float:
    km = row.get("distance_km")
    if km is not None:
        return float(km)
    miles = row.get("distance_miles")
    if miles is not None:
        return float(miles) * KM_PER_MILE
    return 0.0


def sum_running_km(
    *,
    week_start: date,
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
    run_pace_max_sec_mi: float = DEFAULT_RUN_PACE_MAX_SEC_MI,
) -> float:
    """Sum running km for ``[week_start, week_start + 6]`` from sessions + cardio.

    Only *over-recorded* distances (an implausibly fast pace, below the min band —
    typically GPS multiplication) are excluded so they cannot corrupt the total.
    An implausibly *slow* pace (walk breaks / a paused timer) is kept: the athlete
    really covered that distance, so a genuine logged run is never silently zeroed.
    ``run_pace_max_sec_mi`` is retained for signature compatibility with the daily
    pipeline (it drives the separate data-quality note, not this sum).
    """
    week_end = week_start + timedelta(days=6)
    total = 0.0
    for row in running_sessions:
        sd = row.get("session_date") or row.get("event_date")
        if sd is None:
            continue
        if isinstance(sd, str):
            sd = date.fromisoformat(sd[:10])
        if week_start <= sd <= week_end:
            total += _km_from_row(row)
    for row in cardio_events or ():
        ed = row.get("event_date")
        if ed is None:
            continue
        if isinstance(ed, str):
            ed = date.fromisoformat(ed[:10])
        activity = str(row.get("activity_type") or "").lower()
        if week_start <= ed <= week_end and "run" in activity:
            if is_overrecorded_distance(row, run_pace_min_sec_mi=run_pace_min_sec_mi):
                continue
            total += _km_from_row(row)
    return round(total, 2)


def check_mileage_ramp(
    *,
    run_date: date,
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
    max_increase_pct: float = DEFAULT_MAX_WEEKLY_INCREASE_PCT,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
    run_pace_max_sec_mi: float = DEFAULT_RUN_PACE_MAX_SEC_MI,
) -> dict[str, Any]:
    """Return mileage_check block for briefing / daily_goal_snapshot."""
    this_start = iso_week_start(run_date)
    last_start = this_start - timedelta(days=7)
    this_km = sum_running_km(
        week_start=this_start,
        running_sessions=running_sessions,
        cardio_events=cardio_events,
        run_pace_min_sec_mi=run_pace_min_sec_mi,
        run_pace_max_sec_mi=run_pace_max_sec_mi,
    )
    last_km = sum_running_km(
        week_start=last_start,
        running_sessions=running_sessions,
        cardio_events=cardio_events,
        run_pace_min_sec_mi=run_pace_min_sec_mi,
        run_pace_max_sec_mi=run_pace_max_sec_mi,
    )
    change_pct: float | None = None
    if last_km > 0:
        change_pct = round((this_km - last_km) / last_km * 100.0, 1)
    flag: str | None = None
    if change_pct is not None and change_pct > max_increase_pct:
        flag = "mileage_spike"
    return {
        "flag": flag,
        "this_week_km": this_km,
        "last_week_km": last_km,
        "change_pct": change_pct,
    }

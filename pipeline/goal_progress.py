"""Structured weekly goal progress and today's focus (Slice A).

Computes ``goals_status``, ``mileage_check``, and ``todays_focus`` from
active ``goals`` rows plus strength/cardio/running session data. The daily
pipeline persists a ``daily_goal_snapshot`` and injects these blocks into
the briefing prompt — the LLM narrates; it does not invent session counts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.mileage_ramp import check_mileage_ramp, iso_week_start
from pipeline.schedule_context import apply_schedule_to_focus_parts, is_goal_blocked
from pipeline.features import calendar_week_strength_volume

STRENGTH_GOAL = "strength"
RUNNING_GOAL_TYPES = ("running_long", "running_easy", "running_interval")
RUN_TYPE_BY_GOAL = {
    "running_long": "long",
    "running_easy": "easy",
    "running_interval": "interval",
}


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _week_dates(week_start: date) -> set[date]:
    return {week_start + timedelta(days=i) for i in range(7)}


def _running_done(
    goal_type: str,
    *,
    week_start: date,
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    run_type = RUN_TYPE_BY_GOAL.get(goal_type)
    if run_type is None:
        return False
    week = _week_dates(week_start)
    for row in running_sessions:
        sd = _parse_date(row.get("session_date"))
        if sd in week and row.get("run_type") == run_type:
            return True
    for row in cardio_events or ():
        ed = _parse_date(row.get("event_date"))
        if ed not in week:
            continue
        activity = str(row.get("activity_type") or "").lower()
        if "run" not in activity:
            continue
        if run_type == "long" and ("long" in activity or (row.get("distance_miles") or 0) >= 6):
            return True
        if run_type == "interval" and ("interval" in activity or "tempo" in activity):
            return True
        if run_type == "easy" and run_type in activity:
            return True
        if run_type == "easy" and "run" in activity:
            return True
    return False


def _target_label(goal: Mapping[str, Any]) -> str:
    label = goal.get("target_label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    tmin = goal.get("target_min")
    tmax = goal.get("target_max")
    if tmin is not None and tmax is not None and tmin != tmax:
        return f"{tmin}-{tmax}x"
    if tmin is not None:
        return f"{tmin}x"
    return "?"


def _pace_status(
    *,
    completed: int,
    target_min: int | None,
    run_date: date,
    week_start: date,
) -> str:
    """Map progress to done / not_yet / behind / urgent."""
    if target_min is not None and completed >= target_min:
        return "done"
    day_idx = (run_date - week_start).days  # 0=Mon … 6=Sun
    if target_min is None:
        return "not_yet"
    remaining_needed = target_min - completed
    days_left = 6 - day_idx
    if day_idx <= 2:
        return "not_yet"
    if days_left < remaining_needed:
        return "urgent"
    if completed < target_min and day_idx >= 3:
        return "behind"
    return "not_yet"


def compute_goal_status(
    *,
    run_date: date,
    goals: Sequence[Mapping[str, Any]],
    strength_events: Sequence[Mapping[str, Any]],
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
    exceptions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build ``goals_status`` JSON for briefing / snapshot."""
    week_start = iso_week_start(run_date)
    strength_dates = calendar_week_strength_volume(
        strength_events, week_start=week_start
    )["session_dates"]
    strength_completed = len(strength_dates)
    status: dict[str, Any] = {}

    for goal in goals:
        if not goal.get("is_active", True):
            continue
        gtype = goal.get("goal_type")
        if not isinstance(gtype, str):
            continue
        eff_from = _parse_date(goal.get("effective_from"))
        eff_until = _parse_date(goal.get("effective_until"))
        if eff_from is not None and run_date < eff_from:
            continue
        if eff_until is not None and run_date > eff_until:
            continue
        blocked = is_goal_blocked(gtype, run_date=run_date, exceptions=exceptions or ())
        if gtype == STRENGTH_GOAL:
            tmin = goal.get("target_min")
            tmin_int = int(tmin) if tmin is not None else None
            st = _pace_status(
                completed=strength_completed,
                target_min=tmin_int,
                run_date=run_date,
                week_start=week_start,
            )
            if blocked:
                st = "skipped"
            status["strength"] = {
                "completed": strength_completed,
                "target": _target_label(goal),
                "status": st,
                "schedule_note": blocked,
            }
        elif gtype in RUNNING_GOAL_TYPES:
            done = _running_done(
                gtype,
                week_start=week_start,
                running_sessions=running_sessions,
                cardio_events=cardio_events,
            )
            st = "done" if done else "not_yet"
            if not done:
                st = _pace_status(
                    completed=0,
                    target_min=1,
                    run_date=run_date,
                    week_start=week_start,
                )
            if blocked:
                st = "skipped"
            if "running" not in status:
                status["running"] = {}
            key = RUN_TYPE_BY_GOAL[gtype]
            status["running"][key] = {
                "done": done,
                "status": st,
                "schedule_note": blocked,
            }
    return status


def suggest_todays_focus(
    *,
    goals_status: Mapping[str, Any],
    run_date: date,
    exceptions: Sequence[Mapping[str, Any]] | None = None,
    interventions: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Deterministic focus string from pre-computed goal status."""
    parts: list[str] = []

    strength = goals_status.get("strength")
    if isinstance(strength, dict):
        st = strength.get("status")
        if st in ("behind", "urgent", "not_yet"):
            completed = strength.get("completed", 0)
            target = strength.get("target", "?")
            urgency = " needed" if st == "urgent" else " session"
            parts.append(f"Strength{urgency} — {completed} of {target} done")

    running = goals_status.get("running")
    if isinstance(running, dict):
        for key, label in (
            ("interval", "Interval run"),
            ("long", "Long run"),
            ("easy", "Easy run"),
        ):
            item = running.get(key)
            if not isinstance(item, dict):
                continue
            if item.get("status") == "skipped":
                note = item.get("schedule_note")
                if note:
                    parts.append(f"{label} skipped ({note})")
                continue
            if item.get("status") in ("behind", "urgent", "not_yet") and not item.get("done"):
                suffix = " still pending" if item.get("status") != "urgent" else " urgent"
                parts.append(f"{label}{suffix}")

    parts = apply_schedule_to_focus_parts(
        parts,
        run_date=run_date,
        exceptions=exceptions or (),
        interventions=interventions,
    )
    if not parts:
        return "On track — no urgent sessions flagged for today."
    return " · ".join(parts)


def compute_weekly_activity_summary(
    *,
    user_id: str,
    week_start: date,
    strength_events: Sequence[Mapping[str, Any]],
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``weekly_activity_summary`` row dict."""
    from pipeline.mileage_ramp import sum_running_km

    week = _week_dates(week_start)
    cardio_min = 0.0
    for row in cardio_events or ():
        ed = _parse_date(row.get("event_date"))
        if ed in week:
            dm = row.get("duration_min")
            if dm is not None:
                cardio_min += float(dm)
    running_km = sum_running_km(
        week_start=week_start,
        running_sessions=running_sessions,
        cardio_events=cardio_events,
    )
    strength_volume = calendar_week_strength_volume(
        strength_events, week_start=week_start
    )
    strength_dates = strength_volume["session_dates"]
    return {
        "user_id": user_id,
        "week_start": week_start,
        "strength_sessions": len(strength_dates),
        "running_km": running_km,
        "cardio_minutes": round(cardio_min, 1),
        "summary_json": {
            "strength_session_dates": sorted(d.isoformat() for d in strength_dates),
            "strength_short_tons": strength_volume["strength_short_tons"],
            "strength_hard_sets": strength_volume["strength_hard_sets"],
            "strength_volume_lbs": strength_volume["strength_volume_lbs"],
        },
    }


def build_daily_goal_snapshot(
    *,
    user_id: str,
    run_date: date,
    goals: Sequence[Mapping[str, Any]],
    strength_events: Sequence[Mapping[str, Any]],
    running_sessions: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]] | None = None,
    exceptions: Sequence[Mapping[str, Any]] | None = None,
    interventions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full snapshot row for ``daily_goal_snapshot`` + briefing injection."""
    goals_status = compute_goal_status(
        run_date=run_date,
        goals=goals,
        strength_events=strength_events,
        running_sessions=running_sessions,
        cardio_events=cardio_events,
        exceptions=exceptions,
    )
    mileage_check = check_mileage_ramp(
        run_date=run_date,
        running_sessions=running_sessions,
        cardio_events=cardio_events,
    )
    todays_focus = suggest_todays_focus(
        goals_status=goals_status,
        run_date=run_date,
        exceptions=exceptions,
        interventions=interventions,
    )
    return {
        "user_id": user_id,
        "snapshot_date": run_date,
        "goals_status": goals_status,
        "mileage_check": mileage_check,
        "todays_focus": todays_focus,
    }

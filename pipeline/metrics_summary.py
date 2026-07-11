"""Deterministic "At a Glance" metrics summary for the daily briefing.

The daily briefing **leads** with a short, deterministic list of key numbers so
the athlete gets a quick-glance summary before reading the LLM prose. Per
``.cursor/rules/soma.mdc`` these numbers are PRE-COMPUTED — sourced from the
current **ISO calendar week** (Mon–Sun) activity rollup, today's
``daily_health_metrics``, the rules-engine ``Flag`` list, and the goal snapshot.
The LLM never produces them; it only narrates afterwards.

Everything here is **pure** (no IO) and tolerant of missing data: a metric line
is emitted only when its underlying value is present. The red-flag line is always
emitted (``None`` when clear) so the summary is never empty and the reader always
gets an explicit "all clear" signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.features import LBS_PER_SHORT_TON, as_date, calendar_week_strength_volume
from pipeline.mileage_ramp import iso_week_start
from pipeline.rules import Flag
from pipeline.units import km_to_miles
from pipeline.workload_pace import calendar_week_cardio_load, calendar_week_cardio_sessions, pace_status_message

GLANCE_HEADING = "## At a Glance"

# Flag severities that count as a "major red flag" worth surfacing up top.
_RED_FLAG_SEVERITIES = frozenset({"warning", "alert"})


def _num(mapping: Mapping[str, Any], key: str) -> float | None:
    """Return a float for a numeric (non-bool) value at ``key``, else ``None``."""
    value = mapping.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fmt_num(value: float, *, decimals: int = 0) -> str:
    """Format a float, dropping the decimal point when it is a whole number."""
    if decimals == 0 or float(value).is_integer():
        return f"{round(value):,}"
    return f"{value:,.{decimals}f}"


def _in_calendar_week(event_date: date, *, week_start: date) -> bool:
    return week_start <= event_date <= week_start + timedelta(days=6)


def count_run_sessions_this_week(
    cardio_events: Sequence[Mapping[str, Any]],
    running_sessions: Sequence[Mapping[str, Any]] | None = None,
    *,
    as_of: date,
) -> int:
    """Count distinct calendar days with a run in the current Mon–Sun week."""
    week_start = iso_week_start(as_of)
    days: set[date] = set()
    for row in cardio_events or ():
        d = as_date(row.get("event_date"))
        if d is None or not _in_calendar_week(d, week_start=week_start):
            continue
        if is_strength_like_cardio_activity(row.get("activity_type")):
            continue
        if "run" in str(row.get("activity_type") or "").lower():
            days.add(d)
    for row in running_sessions or ():
        d = as_date(row.get("session_date"))
        if d is not None and _in_calendar_week(d, week_start=week_start):
            days.add(d)
    return len(days)


def calendar_week_glance_activity(
    *,
    as_of: date,
    strength_events: Sequence[Mapping[str, Any]] = (),
    cardio_events: Sequence[Mapping[str, Any]] = (),
    running_sessions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pre-compute Mon–Sun glance activity numbers for :func:`build_glance_metrics`."""
    week_start = iso_week_start(as_of)
    strength = calendar_week_strength_volume(strength_events, week_start=week_start)
    return {
        "week_start": week_start.isoformat(),
        "run_sessions": count_run_sessions_this_week(
            cardio_events, running_sessions, as_of=as_of
        ),
        "strength_sessions": len(strength["session_dates"]),
        "strength_hard_sets": strength["strength_hard_sets"],
        "strength_tonnage_short_tons": strength["strength_short_tons"],
        "strength_volume_lbs": strength["strength_volume_lbs"],
        "cardio_sessions": calendar_week_cardio_sessions(
            cardio_events, week_start=week_start
        ),
        "cardio_minutes": calendar_week_cardio_load(
            cardio_events, week_start=week_start, mode=None, metric="minutes"
        ),
    }


def _red_flag_line(flags: Sequence[Flag]) -> tuple[str, str]:
    """Summarize warning/alert flags as a single glance line.

    Always returns a ``(label, value)`` pair; the value is the literal string
    ``"None"`` when there are no warning/alert flags so the summary always ends
    with an explicit status.
    """
    major = [f for f in flags if f.severity in _RED_FLAG_SEVERITIES]
    if not major:
        return ("Red flags", "None")
    codes = ", ".join(f.code for f in major)
    return ("Red flags", f"{len(major)} — {codes}")


def build_glance_metrics(
    *,
    features: Mapping[str, Any],
    daily_metrics: Mapping[str, Any] | None = None,
    flags: Sequence[Flag] = (),
    goal_snapshot: Mapping[str, Any] | None = None,
    week_activity: Mapping[str, Any] | None = None,
    run_sessions_7d: int | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
    workload_pace: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Build the ordered ``(label, value)`` pairs for the glance summary.

    Training lines prefer ``week_activity`` (ISO calendar week). ``run_sessions_7d``
    remains as a thin alias for callers that only have a run count.
    """
    metrics = daily_metrics or {}
    lines: list[tuple[str, str]] = []
    week = week_activity or {}

    run_sessions = week.get("run_sessions")
    if run_sessions is None:
        run_sessions = run_sessions_7d
    if run_sessions is not None:
        lines.append(("Runs (this week)", str(int(run_sessions))))

    strength_sessions = _num(week, "strength_sessions")
    if strength_sessions is not None:
        detail = f"{_fmt_num(strength_sessions)} session{'' if strength_sessions == 1 else 's'}"
        hard_sets = _num(week, "strength_hard_sets")
        if hard_sets is not None:
            detail += f" · {_fmt_num(hard_sets)} hard sets"
        lines.append(("Strength (this week)", detail))

    cardio_sessions = _num(week, "cardio_sessions")
    cardio_minutes = _num(week, "cardio_minutes")
    if cardio_sessions is not None or cardio_minutes is not None:
        parts: list[str] = []
        if cardio_sessions is not None:
            parts.append(f"{_fmt_num(cardio_sessions)} session{'' if cardio_sessions == 1 else 's'}")
        if cardio_minutes is not None:
            parts.append(f"{_fmt_num(cardio_minutes)} min")
        lines.append(("Cardio (this week)", " · ".join(parts)))

    tonnage = _num(week, "strength_tonnage_short_tons")
    if tonnage is not None:
        lbs = _num(week, "strength_volume_lbs")
        if lbs is None:
            lbs = tonnage * LBS_PER_SHORT_TON
        lines.append(
            (
                "Lifting tonnage (this week)",
                f"{_fmt_num(tonnage, decimals=1)} short tons ({_fmt_num(lbs)} lb)",
            )
        )

    if strength_progress:
        wow = strength_progress.get("week_over_week_change_pct")
        week_vol = strength_progress.get("this_week_volume_lbs")
        if week_vol is not None and tonnage is None:
            # Fallback when week_activity omitted but strength_progress is present.
            detail = f"{_fmt_num(week_vol)} lb this calendar week"
            if isinstance(wow, (int, float)):
                detail += f" ({wow:+.1f}% vs last week)"
            lines.append(("Weekly lifting volume", detail))
        elif week_vol is not None and isinstance(wow, (int, float)):
            lines.append(
                (
                    "Lifting vs last week",
                    f"{wow:+.1f}% ({_fmt_num(week_vol)} lb this week)",
                )
            )

    if training_phase and isinstance(training_phase.get("active"), Mapping):
        active = training_phase["active"]
        name = active.get("name")
        phase_type = active.get("phase_type")
        weeks_remaining = active.get("weeks_remaining")
        if name:
            detail = str(name)
            if phase_type:
                detail += f" ({phase_type})"
            if weeks_remaining is not None:
                detail += f" · {weeks_remaining} week(s) left"
            lines.append(("Training phase", detail))

    if workload_pace:
        lifting = workload_pace.get("lifting")
        if isinstance(lifting, Mapping):
            lines.append(("Lifting pace", pace_status_message(lifting)))
        cardio = workload_pace.get("cardio")
        if isinstance(cardio, Mapping):
            lines.append(("Cardio pace", pace_status_message(cardio)))

    if goal_snapshot:
        mileage = goal_snapshot.get("mileage_check")
        if isinstance(mileage, Mapping):
            this_week_km = _num(mileage, "this_week_km")
            if this_week_km is not None:
                miles = km_to_miles(this_week_km)
                lines.append(("Run distance (this week)", f"{_fmt_num(miles, decimals=1)} mi"))

    resting_hr = _num(metrics, "resting_hr")
    if resting_hr is not None:
        lines.append(("Resting HR", f"{_fmt_num(resting_hr)} bpm"))

    hrv = _num(metrics, "hrv_rmssd")
    if hrv is not None:
        lines.append(("HRV (last night)", f"{_fmt_num(hrv)} ms"))

    sleep = _num(metrics, "sleep_hours")
    if sleep is not None:
        lines.append(("Sleep (last night)", f"{_fmt_num(sleep, decimals=1)} h"))

    weight = _num(metrics, "body_weight_lbs")
    if weight is not None:
        lines.append(("Body weight", f"{_fmt_num(weight, decimals=1)} lb"))

    readiness = _num(features, "overall_readiness_score")
    if readiness is not None:
        lines.append(("Readiness", f"{_fmt_num(readiness)}/100"))

    lines.append(_red_flag_line(flags))
    return lines


def render_glance_block(metrics: Sequence[tuple[str, str]]) -> str:
    """Render ``(label, value)`` pairs as a Markdown heading + bullet list.

    Returns an empty string for no metrics. The blank line after the heading keeps
    it a separate block so :func:`pipeline.delivery.coaching_note_to_html` renders
    it as an ``<h2>`` followed by a ``<ul>``.
    """
    if not metrics:
        return ""
    bullets = "\n".join(f"- **{label}:** {value}" for label, value in metrics)
    return f"{GLANCE_HEADING}\n\n{bullets}"


def format_glance_section(
    *,
    features: Mapping[str, Any],
    daily_metrics: Mapping[str, Any] | None = None,
    flags: Sequence[Flag] = (),
    goal_snapshot: Mapping[str, Any] | None = None,
    week_activity: Mapping[str, Any] | None = None,
    run_sessions_7d: int | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
    workload_pace: Mapping[str, Any] | None = None,
) -> str:
    """Convenience: build the glance metrics and render them to a Markdown block."""
    return render_glance_block(
        build_glance_metrics(
            features=features,
            daily_metrics=daily_metrics,
            flags=flags,
            goal_snapshot=goal_snapshot,
            week_activity=week_activity,
            run_sessions_7d=run_sessions_7d,
            strength_progress=strength_progress,
            training_phase=training_phase,
            workload_pace=workload_pace,
        )
    )

"""Deterministic "At a Glance" metrics summary for the daily briefing.

The daily briefing **leads** with a short, deterministic list of key numbers so
the athlete gets a quick-glance summary before reading the LLM prose. Per
``.cursor/rules/soma.mdc`` these numbers are PRE-COMPUTED — sourced from the
``daily_features`` row, today's ``daily_health_metrics`` rollup, the rules-engine
``Flag`` list, and the goal snapshot. The LLM never produces them; it only
narrates afterwards.

Everything here is **pure** (no IO) and tolerant of missing data: a metric line
is emitted only when its underlying value is present. The red-flag line is always
emitted (``None`` when clear) so the summary is never empty and the reader always
gets an explicit "all clear" signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pipeline.features import ACUTE_WINDOW_DAYS, LBS_PER_SHORT_TON, as_date
from pipeline.rules import Flag
from pipeline.units import km_to_miles

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


def _in_last_7d(event_date: date, *, as_of: date) -> bool:
    delta = (as_of - event_date).days
    return 0 <= delta < ACUTE_WINDOW_DAYS


def count_run_sessions_7d(
    cardio_events: Sequence[Mapping[str, Any]],
    running_sessions: Sequence[Mapping[str, Any]] | None = None,
    *,
    as_of: date,
) -> int:
    """Count distinct calendar days with a run in the trailing 7-day window.

    A run is any ``running_sessions`` row or a ``cardio_events`` row whose
    ``activity_type`` contains "run" (same detection as ``goal_progress`` and
    ``mileage_ramp``). Counting distinct days (rather than rows) keeps the number
    intuitive when a source emits several fragments for one outing.
    """
    days: set[date] = set()
    for row in cardio_events or ():
        d = as_date(row.get("event_date"))
        if d is None or not _in_last_7d(d, as_of=as_of):
            continue
        if "run" in str(row.get("activity_type") or "").lower():
            days.add(d)
    for row in running_sessions or ():
        d = as_date(row.get("session_date"))
        if d is not None and _in_last_7d(d, as_of=as_of):
            days.add(d)
    return len(days)


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
    run_sessions_7d: int | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Build the ordered ``(label, value)`` pairs for the glance summary.

    Only metrics with underlying data are included, except the red-flag line which
    is always emitted last so the summary always ends with an explicit status.
    """
    metrics = daily_metrics or {}
    lines: list[tuple[str, str]] = []

    if run_sessions_7d is not None:
        lines.append(("Runs (7d)", str(run_sessions_7d)))

    strength_sessions = _num(features, "strength_sessions_7d")
    if strength_sessions is not None:
        detail = f"{_fmt_num(strength_sessions)} session{'' if strength_sessions == 1 else 's'}"
        hard_sets = _num(features, "strength_hard_sets_7d")
        if hard_sets is not None:
            detail += f" · {_fmt_num(hard_sets)} hard sets"
        lines.append(("Strength (7d)", detail))

    cardio_sessions = _num(features, "cardio_sessions_7d")
    cardio_minutes = _num(features, "cardio_minutes_7d")
    if cardio_sessions is not None or cardio_minutes is not None:
        parts: list[str] = []
        if cardio_sessions is not None:
            parts.append(f"{_fmt_num(cardio_sessions)} session{'' if cardio_sessions == 1 else 's'}")
        if cardio_minutes is not None:
            parts.append(f"{_fmt_num(cardio_minutes)} min")
        lines.append(("Cardio (7d)", " · ".join(parts)))

    tonnage = _num(features, "strength_tonnage_7d")
    if tonnage is not None:
        lbs = tonnage * LBS_PER_SHORT_TON
        lines.append(
            (
                "Lifting tonnage (7d)",
                f"{_fmt_num(tonnage, decimals=1)} short tons ({_fmt_num(lbs)} lb)",
            )
        )

    if strength_progress:
        wow = strength_progress.get("week_over_week_change_pct")
        week_vol = strength_progress.get("this_week_volume_lbs")
        if week_vol is not None:
            detail = f"{_fmt_num(week_vol)} lb this calendar week"
            if isinstance(wow, (int, float)):
                detail += f" ({wow:+.1f}% vs last week)"
            lines.append(("Weekly lifting volume", detail))
        top = strength_progress.get("top_exercises")
        if isinstance(top, list) and top:
            highlights: list[str] = []
            for ex in top[:3]:
                if not isinstance(ex, Mapping):
                    continue
                name = ex.get("exercise_name")
                weight = ex.get("latest_top_weight_lbs")
                delta = ex.get("weight_delta_vs_prior")
                if not name or weight is None:
                    continue
                piece = f"{name} {_fmt_num(weight, decimals=1)} lb"
                if isinstance(delta, (int, float)) and delta != 0:
                    piece += f" ({delta:+.1f} lb vs prior)"
                highlights.append(piece)
            if highlights:
                lines.append(("Key lifts (latest)", " · ".join(highlights)))

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
    run_sessions_7d: int | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
) -> str:
    """Convenience: build the glance metrics and render them to a Markdown block."""
    return render_glance_block(
        build_glance_metrics(
            features=features,
            daily_metrics=daily_metrics,
            flags=flags,
            goal_snapshot=goal_snapshot,
            run_sessions_7d=run_sessions_7d,
            strength_progress=strength_progress,
            training_phase=training_phase,
        )
    )

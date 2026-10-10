"""Deterministic "At a Glance" metrics summary for the daily briefing.

The daily briefing **leads** with weight trend and last-7-day training load
versus the rolling 4-week average. Per ``.cursor/rules/soma.mdc`` these numbers
are PRE-COMPUTED. The LLM never produces them; it only narrates afterwards.

Recovery metrics, session counts, and week-over-week pace lights are not
rendered here. A weight line is always present (``No weigh-in logged`` when
the series is empty).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.features import as_date, calendar_week_strength_volume
from pipeline.mileage_ramp import iso_week_start
from pipeline.rules import Flag
from pipeline.weight_trend import format_weight_glance
from pipeline.workload_pace import (
    calendar_week_cardio_load,
    calendar_week_cardio_sessions,
    format_load_vs_four_week,
)

GLANCE_HEADING = "## At a Glance"


def _num(mapping: Mapping[str, Any], key: str) -> float | None:
    """Return a float for a numeric (non-bool) value at ``key``, else ``None``."""
    value = mapping.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


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
    weight_trend: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Build glance lines: weight trend, then load versus the 4-week average.

    ``features``, ``flags``, ``goal_snapshot``, ``week_activity``,
    ``run_sessions_7d``, and ``strength_progress`` stay on the signature so
    existing callers keep working. They are not rendered.
    """
    del features, flags, goal_snapshot, week_activity, run_sessions_7d, strength_progress
    lines: list[tuple[str, str]] = []

    trend = weight_trend
    if trend is None and daily_metrics:
        weight = _num(daily_metrics, "body_weight_lbs")
        if weight is not None:
            trend = {"latest_lbs": weight, "readings_7d": 1, "readings_28d": 1}
    lines.extend(format_weight_glance(trend))

    if workload_pace:
        cardio = workload_pace.get("cardio")
        if isinstance(cardio, Mapping):
            cardio_line = format_load_vs_four_week(cardio)
            if cardio_line:
                lines.append(("Cardio load", cardio_line))
        lifting = workload_pace.get("lifting")
        if isinstance(lifting, Mapping):
            lifting_line = format_load_vs_four_week(lifting)
            if lifting_line:
                lines.append(("Lifting load", lifting_line))

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
    weight_trend: Mapping[str, Any] | None = None,
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
            weight_trend=weight_trend,
        )
    )

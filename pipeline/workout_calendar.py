"""Workout calendar days + streaks from strength and cardio events.

Combines Hevy lifting (``strength_events``), cardio (``cardio_events`` including
Apple Health / Strava / Fitbit-via-Health-Sync), into one activity-day map for
dashboard month grids and streak counters.

Strength-typed Apple Health workouts are ignored on days that already have Hevy
sets (same rule as training-load dedup). Fitbit-only walks and other activities
still count as workout days.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pipeline.cardio_quality import is_strength_like_cardio_activity
from pipeline.features import as_date
from pipeline.mileage_ramp import iso_week_start
from pipeline.source_priority import BRIDGE_SOURCE_APPS

DayKind = Literal["lifting", "cardio", "both"]
ActivityBucket = Literal["lifting", "cardio", "fitbit", "other"]


def _normalize_source_app(source_app: object) -> str:
    if not isinstance(source_app, str):
        return ""
    return " ".join(source_app.strip().lower().split())


def is_fitbit_origin_activity(source_app: object) -> bool:
    """True when ``source_app`` looks like Fitbit / Health Sync / Google Fit bridge."""
    normalized = _normalize_source_app(source_app)
    if not normalized:
        return False
    for bridge in BRIDGE_SOURCE_APPS:
        if bridge in normalized:
            return True
    return False


def _activity_label(activity_type: Any) -> str | None:
    if not isinstance(activity_type, str):
        return None
    label = " ".join(activity_type.strip().split())
    return label or None


def _empty_day() -> dict[str, Any]:
    return {
        "lifting": False,
        "cardio": False,
        "fitbit": False,
        "kinds": [],
        "activity_types": [],
        "sources": [],
    }


def build_workout_day_map(
    strength_events: Iterable[Mapping[str, Any]],
    cardio_events: Iterable[Mapping[str, Any]],
) -> dict[date, dict[str, Any]]:
    """Aggregate event rows into per-calendar-day workout flags.

    Returns ``{date: {lifting, cardio, fitbit, kinds, activity_types, sources}}``.
    ``kinds`` is a display category: ``lifting`` / ``cardio`` / ``both``.
    """
    days: dict[date, dict[str, Any]] = defaultdict(_empty_day)
    lifting_dates: set[date] = set()

    for row in strength_events:
        d = as_date(row.get("event_date"))
        if d is None:
            continue
        lifting_dates.add(d)
        entry = days[d]
        entry["lifting"] = True
        if "hevy" not in entry["sources"]:
            entry["sources"].append("hevy")

    for row in cardio_events:
        d = as_date(row.get("event_date"))
        if d is None:
            continue
        activity_type = row.get("activity_type")
        if is_strength_like_cardio_activity(activity_type) and d in lifting_dates:
            continue
        entry = days[d]
        fitbit = is_fitbit_origin_activity(row.get("source_app"))
        if fitbit:
            entry["fitbit"] = True
            if "fitbit" not in entry["sources"]:
                entry["sources"].append("fitbit")
        else:
            source = row.get("source")
            if isinstance(source, str) and source.strip():
                src = source.strip().lower()
                if src not in entry["sources"]:
                    entry["sources"].append(src)
            source_app = row.get("source_app")
            if isinstance(source_app, str) and source_app.strip():
                app = source_app.strip()
                if app not in entry["sources"]:
                    entry["sources"].append(app)

        # Strength-like cardio with no Hevy that day still marks a lifting day.
        if is_strength_like_cardio_activity(activity_type):
            entry["lifting"] = True
        else:
            entry["cardio"] = True

        label = _activity_label(activity_type)
        if label and label not in entry["activity_types"]:
            entry["activity_types"].append(label)

    # Finalize kinds for every day that has any activity.
    result: dict[date, dict[str, Any]] = {}
    for d, entry in days.items():
        lifting = bool(entry["lifting"])
        cardio = bool(entry["cardio"])
        if not lifting and not cardio and not entry["fitbit"]:
            continue
        # Fitbit-only rows with an unknown/empty activity still count as cardio days.
        if entry["fitbit"] and not lifting and not cardio:
            cardio = True
            entry["cardio"] = True
        if lifting and cardio:
            kind: DayKind = "both"
        elif lifting:
            kind = "lifting"
        else:
            kind = "cardio"
        entry["kinds"] = [kind]
        entry["kind"] = kind
        result[d] = dict(entry)
    return result


def _consecutive_step_streak(
    active: set[date],
    *,
    as_of: date,
    step_days: int,
) -> tuple[int, int]:
    """Return (current, longest) streaks over dates spaced by ``step_days``."""
    if not active:
        return 0, 0
    start = as_of if as_of in active else as_of - timedelta(days=step_days)
    current = 0
    cursor = start
    while cursor in active:
        current += 1
        cursor -= timedelta(days=step_days)

    sorted_pts = sorted(active)
    longest = 1
    run = 1
    for prev, nxt in zip(sorted_pts, sorted_pts[1:]):
        if (nxt - prev).days == step_days:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return current, longest


def compute_streaks(
    workout_days: Mapping[date, Any],
    *,
    as_of: date,
) -> dict[str, int]:
    """Current and longest streaks of consecutive workout days.

    Current streak: walk backwards from ``as_of``. If ``as_of`` has no workout yet,
    start from ``as_of - 1`` so an incomplete today does not reset the streak.
    Longest streak: max consecutive run among dates ≤ ``as_of``.
    """
    active = {d for d in workout_days if d <= as_of}
    if not active:
        return {"current_streak": 0, "longest_streak": 0, "workout_days_count": 0}
    current, longest = _consecutive_step_streak(active, as_of=as_of, step_days=1)
    return {
        "current_streak": current,
        "longest_streak": longest,
        "workout_days_count": len(active),
    }


def compute_week_streaks(
    workout_days: Mapping[date, Mapping[str, Any]],
    *,
    as_of: date,
) -> dict[str, int]:
    """Consecutive ISO weeks with workouts, plus lifting/cardio breakdowns.

    A week counts when any day in Mon–Sun (≤ ``as_of``) has the relevant flag.
    Incomplete current week does not reset the streak (same grace as day streaks).
    """
    as_of_week = iso_week_start(as_of)
    any_weeks: set[date] = set()
    lift_weeks: set[date] = set()
    cardio_weeks: set[date] = set()
    for d, info in workout_days.items():
        if d > as_of:
            continue
        week = iso_week_start(d)
        any_weeks.add(week)
        if info.get("lifting"):
            lift_weeks.add(week)
        if info.get("cardio"):
            cardio_weeks.add(week)

    if not any_weeks:
        return {
            "current_week_streak": 0,
            "longest_week_streak": 0,
            "lifting_week_streak": 0,
            "cardio_week_streak": 0,
            "workout_weeks_count": 0,
        }

    current, longest = _consecutive_step_streak(
        any_weeks, as_of=as_of_week, step_days=7
    )
    lift_current, _ = _consecutive_step_streak(
        lift_weeks, as_of=as_of_week, step_days=7
    )
    cardio_current, _ = _consecutive_step_streak(
        cardio_weeks, as_of=as_of_week, step_days=7
    )
    return {
        "current_week_streak": current,
        "longest_week_streak": longest,
        "lifting_week_streak": lift_current,
        "cardio_week_streak": cardio_current,
        "workout_weeks_count": len(any_weeks),
    }


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Inclusive first/last calendar dates for ``year``-``month``."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def build_month_grid(
    workout_days: Mapping[date, Mapping[str, Any]],
    *,
    year: int,
    month: int,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Rows for a month calendar heatmap (Mon-start weeks).

    Each cell: ``date``, ``weekday`` (0=Mon), ``week_index``, ``in_month``,
    ``worked_out``, ``kind``, ``label``, ``future``.
    """
    start, end = month_bounds(year, month)
    # Pad to Monday-start / Sunday-end grid.
    grid_start = start - timedelta(days=start.weekday())
    grid_end = end + timedelta(days=(6 - end.weekday()))
    rows: list[dict[str, Any]] = []
    cursor = grid_start
    week_index = 0
    while cursor <= grid_end:
        info = workout_days.get(cursor)
        worked = info is not None
        kind = str(info.get("kind") or "") if info else ""
        types = list(info.get("activity_types") or []) if info else []
        if worked and info and info.get("lifting") and "Lifting" not in types:
            types = ["Lifting", *types]
        label = ", ".join(types) if types else ("Workout" if worked else "Rest")
        future = as_of is not None and cursor > as_of
        rows.append(
            {
                "date": cursor,
                "date_iso": cursor.isoformat(),
                "day": cursor.day,
                "weekday": cursor.weekday(),
                "weekday_name": cursor.strftime("%a"),
                "week_index": week_index,
                "in_month": start <= cursor <= end,
                "worked_out": worked and not future,
                "kind": kind if worked and not future else "",
                "label": label if not future else "Future",
                "future": future,
                "activity_types": types,
                "lifting": bool(info.get("lifting")) if info and not future else False,
                "cardio": bool(info.get("cardio")) if info and not future else False,
                "fitbit": bool(info.get("fitbit")) if info and not future else False,
            }
        )
        if cursor.weekday() == 6:
            week_index += 1
        cursor += timedelta(days=1)
    return rows


def build_workout_calendar(
    strength_events: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    include_previous_month: bool = True,
) -> dict[str, Any]:
    """Full dashboard payload: day map, week streaks, current (+ previous) month grids."""
    day_map = build_workout_day_map(strength_events, cardio_events)
    week_streaks = compute_week_streaks(day_map, as_of=as_of)
    day_streaks = compute_streaks(day_map, as_of=as_of)

    months: list[dict[str, Any]] = []
    year, month = as_of.year, as_of.month
    month_specs = [(year, month)]
    if include_previous_month:
        month_specs.insert(0, previous_month(year, month))

    for y, m in month_specs:
        start, end = month_bounds(y, m)
        grid = build_month_grid(day_map, year=y, month=m, as_of=as_of)
        in_month_workouts = [
            d for d in day_map if start <= d <= end and d <= as_of
        ]
        months.append(
            {
                "year": y,
                "month": m,
                "label": date(y, m, 1).strftime("%B %Y"),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "workout_day_count": len(in_month_workouts),
                "grid": grid,
            }
        )

    # Serialize day map keys for JSON-friendly consumers.
    days_out = {
        d.isoformat(): {
            "lifting": v["lifting"],
            "cardio": v["cardio"],
            "fitbit": v["fitbit"],
            "kind": v["kind"],
            "activity_types": v["activity_types"],
            "sources": v["sources"],
        }
        for d, v in sorted(day_map.items())
        if d <= as_of
    }

    return {
        "as_of": as_of.isoformat(),
        "current_week_streak": week_streaks["current_week_streak"],
        "longest_week_streak": week_streaks["longest_week_streak"],
        "lifting_week_streak": week_streaks["lifting_week_streak"],
        "cardio_week_streak": week_streaks["cardio_week_streak"],
        "workout_weeks_count": week_streaks["workout_weeks_count"],
        # Day streaks retained for debugging / transitional consumers.
        "current_streak": day_streaks["current_streak"],
        "longest_streak": day_streaks["longest_streak"],
        "workout_days_count": day_streaks["workout_days_count"],
        "days": days_out,
        "months": months,
    }

"""Per-exercise and session-volume analytics over ``strength_events``.

Pure functions only — callers pass already-loaded rows (same shapes as
``load_strength_events``). Used by the dashboard, daily briefing glance block,
and weekly summary enrichment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.features import LBS_PER_SHORT_TON, as_date
from pipeline.mileage_ramp import iso_week_start

_HARD_SET_TYPES = frozenset({"working"})
_UPPER_HINTS = (
    "bench",
    "press",
    "chest",
    "shoulder",
    "overhead",
    "ohp",
    "row",
    "pull",
    "lat",
    "pulldown",
    "chin",
    "curl",
    "tricep",
    "bicep",
    "fly",
    "raise",
    "dip",
)
_LOWER_HINTS = (
    "squat",
    "deadlift",
    "rdl",
    "leg",
    "calf",
    "lunge",
    "hip",
    "glute",
    "hamstring",
    "extension",
    "thrust",
)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def is_hard_set(set_type: Any) -> bool:
    """Return True when the set counts toward working volume."""
    return str(set_type or "").strip().lower() in _HARD_SET_TYPES


def infer_session_focus(exercise_name: str) -> str:
    """Classify an exercise as upper, lower, or other using name keywords."""
    name = exercise_name.lower()
    upper = any(h in name for h in _UPPER_HINTS)
    lower = any(h in name for h in _LOWER_HINTS)
    if upper and not lower:
        return "upper"
    if lower and not upper:
        return "lower"
    if upper and lower:
        return "mixed"
    return "other"


def _set_volume(reps: float | None, weight_lbs: float | None) -> float:
    if reps is None or weight_lbs is None:
        return 0.0
    return reps * weight_lbs


def _dominant_focus(volumes: Mapping[str, float]) -> str:
    upper = volumes.get("upper", 0.0)
    lower = volumes.get("lower", 0.0)
    other = volumes.get("other", 0.0) + volumes.get("mixed", 0.0)
    total = upper + lower + other
    if total <= 0:
        return "unknown"
    if upper >= lower and upper >= other and upper / total >= 0.55:
        return "upper"
    if lower >= upper and lower >= other and lower / total >= 0.55:
        return "lower"
    if upper > 0 and lower > 0:
        return "full"
    return "other"


def calendar_week_volume_lbs(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    week_start: date,
) -> float:
    """Working-set volume (lb) for the Mon–Sun calendar week."""
    week = {week_start + timedelta(days=i) for i in range(7)}
    vol = 0.0
    for ev in strength_events:
        d = as_date(ev.get("event_date"))
        if d not in week or not is_hard_set(ev.get("set_type")):
            continue
        vol += _set_volume(_num(ev.get("reps")), _num(ev.get("weight_lbs")))
    return round(vol, 1)


def weekly_strength_rollups(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    weeks: int = 8,
) -> list[dict[str, Any]]:
    """Calendar-week lifting volume with week-over-week change."""
    if weeks < 1:
        return []
    anchor = iso_week_start(as_of)
    rows: list[dict[str, Any]] = []
    for offset in range(weeks - 1, -1, -1):
        week_start = anchor - timedelta(days=7 * offset)
        vol_lb = calendar_week_volume_lbs(strength_events, week_start=week_start)
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "volume_lbs": vol_lb,
                "volume_short_tons": round(vol_lb / LBS_PER_SHORT_TON, 3),
            }
        )
    for idx, row in enumerate(rows):
        if idx == 0:
            row["change_pct"] = None
            continue
        prev = rows[idx - 1]["volume_lbs"]
        cur = row["volume_lbs"]
        if prev and prev > 0:
            row["change_pct"] = round((cur - prev) / prev * 100.0, 1)
        else:
            row["change_pct"] = None
    return rows


def session_day_metrics(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    lookback_days: int = 120,
) -> list[dict[str, Any]]:
    """Per gym day: total volume, dominant focus, and exercise count."""
    start = as_of - timedelta(days=lookback_days - 1)
    by_date: dict[date, dict[str, Any]] = {}
    for ev in strength_events:
        d = as_date(ev.get("event_date"))
        if d is None or d < start or d > as_of:
            continue
        if not is_hard_set(ev.get("set_type")):
            continue
        entry = by_date.setdefault(
            d,
            {
                "event_date": d.isoformat(),
                "volume_lbs": 0.0,
                "focus_volumes": defaultdict(float),
                "exercises": set(),
            },
        )
        name = str(ev.get("exercise_name") or "Unknown").strip() or "Unknown"
        entry["exercises"].add(name)
        vol = _set_volume(_num(ev.get("reps")), _num(ev.get("weight_lbs")))
        entry["volume_lbs"] += vol
        focus = infer_session_focus(name)
        entry["focus_volumes"][focus] += vol
    rows: list[dict[str, Any]] = []
    for d in sorted(by_date):
        entry = by_date[d]
        focus_volumes = dict(entry["focus_volumes"])
        rows.append(
            {
                "event_date": entry["event_date"],
                "volume_lbs": round(entry["volume_lbs"], 1),
                "session_focus": _dominant_focus(focus_volumes),
                "exercise_count": len(entry["exercises"]),
            }
        )
    return rows


def weekly_focus_rollups(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    weeks: int = 8,
) -> list[dict[str, Any]]:
    """Calendar-week volume grouped by dominant session focus (upper/lower/full)."""
    sessions = session_day_metrics(strength_events, as_of=as_of, lookback_days=weeks * 7 + 7)
    by_week: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in sessions:
        d = date.fromisoformat(row["event_date"])
        week_start = iso_week_start(d)
        focus = str(row.get("session_focus") or "unknown")
        by_week[week_start][focus] += float(row.get("volume_lbs") or 0.0)
    anchor = iso_week_start(as_of)
    out: list[dict[str, Any]] = []
    for offset in range(weeks - 1, -1, -1):
        week_start = anchor - timedelta(days=7 * offset)
        buckets = by_week.get(week_start, {})
        out.append(
            {
                "week_start": week_start.isoformat(),
                "upper_volume_lbs": round(buckets.get("upper", 0.0), 1),
                "lower_volume_lbs": round(buckets.get("lower", 0.0), 1),
                "full_volume_lbs": round(buckets.get("full", 0.0), 1),
                "other_volume_lbs": round(
                    buckets.get("other", 0.0) + buckets.get("unknown", 0.0), 1
                ),
            }
        )
    for idx, row in enumerate(out):
        if idx == 0:
            row["upper_change_pct"] = None
            continue
        prev = out[idx - 1]["upper_volume_lbs"]
        cur = row["upper_volume_lbs"]
        if prev and prev > 0:
            row["upper_change_pct"] = round((cur - prev) / prev * 100.0, 1)
        else:
            row["upper_change_pct"] = None
    return out


def exercise_session_series(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    lookback_days: int = 120,
) -> dict[str, list[dict[str, Any]]]:
    """Per exercise, per session day: top working weight and session volume."""
    start = as_of - timedelta(days=lookback_days - 1)
    buckets: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for ev in strength_events:
        d = as_date(ev.get("event_date"))
        if d is None or d < start or d > as_of:
            continue
        if not is_hard_set(ev.get("set_type")):
            continue
        name = str(ev.get("exercise_name") or "Unknown").strip() or "Unknown"
        weight = _num(ev.get("weight_lbs"))
        reps = _num(ev.get("reps"))
        day = buckets[name].setdefault(
            d,
            {"event_date": d.isoformat(), "top_weight_lbs": 0.0, "volume_lbs": 0.0, "hard_sets": 0},
        )
        day["hard_sets"] += 1
        day["volume_lbs"] += _set_volume(reps, weight)
        if weight is not None:
            day["top_weight_lbs"] = max(day["top_weight_lbs"], weight)
    out: dict[str, list[dict[str, Any]]] = {}
    for name, days in buckets.items():
        series = []
        for d in sorted(days):
            row = days[d]
            series.append(
                {
                    "event_date": row["event_date"],
                    "top_weight_lbs": round(row["top_weight_lbs"], 1),
                    "volume_lbs": round(row["volume_lbs"], 1),
                    "hard_sets": row["hard_sets"],
                }
            )
        out[name] = series
    return out


def top_exercises_summary(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    lookback_days: int = 120,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Rank exercises by session frequency with latest progress deltas."""
    series_map = exercise_session_series(
        strength_events, as_of=as_of, lookback_days=lookback_days
    )
    ranked: list[dict[str, Any]] = []
    for name, series in series_map.items():
        if len(series) < 1:
            continue
        latest = series[-1]
        prev = series[-2] if len(series) >= 2 else None
        weight_delta = None
        volume_delta_pct = None
        if prev is not None:
            weight_delta = round(latest["top_weight_lbs"] - prev["top_weight_lbs"], 1)
            prev_vol = prev["volume_lbs"]
            if prev_vol > 0:
                volume_delta_pct = round(
                    (latest["volume_lbs"] - prev_vol) / prev_vol * 100.0, 1
                )
        ranked.append(
            {
                "exercise_name": name,
                "session_count": len(series),
                "latest_date": latest["event_date"],
                "latest_top_weight_lbs": latest["top_weight_lbs"],
                "latest_volume_lbs": latest["volume_lbs"],
                "weight_delta_vs_prior": weight_delta,
                "volume_change_pct_vs_prior": volume_delta_pct,
                "series": series,
            }
        )
    ranked.sort(key=lambda r: (-r["session_count"], r["exercise_name"].lower()))
    return ranked[:limit]


def detect_strength_progress_flags(
  weekly_rollups: Sequence[Mapping[str, Any]],
  *,
  rapid_increase_pct: float = 12.0,
  plateau_weeks: int = 3,
  plateau_band_pct: float = 5.0,
) -> list[dict[str, str]]:
    """Deterministic flags for rapid weekly volume jumps or multi-week plateaus."""
    flags: list[dict[str, str]] = []
    if len(weekly_rollups) >= 2:
        latest = weekly_rollups[-1]
        change = latest.get("change_pct")
        if isinstance(change, (int, float)) and change >= rapid_increase_pct:
            flags.append(
                {
                    "code": "STRENGTH_VOLUME_SPIKE",
                    "message": (
                        f"Calendar-week lifting volume rose {change:.1f}% vs last week — "
                        "worth watching progression pace."
                    ),
                }
            )
    recent = [r for r in weekly_rollups if (r.get("volume_lbs") or 0) > 0][-plateau_weeks:]
    if len(recent) >= plateau_weeks:
        volumes = [float(r["volume_lbs"]) for r in recent]
        avg = sum(volumes) / len(volumes)
        if avg > 0 and all(abs(v - avg) / avg * 100.0 <= plateau_band_pct for v in volumes):
            flags.append(
                {
                    "code": "STRENGTH_VOLUME_PLATEAU",
                    "message": (
                        f"Lifting volume has been flat (~{avg:,.0f} lb/week) for "
                        f"{plateau_weeks} calendar weeks."
                    ),
                }
            )
    return flags


def build_strength_progress_summary(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    lookback_weeks: int = 8,
) -> dict[str, Any]:
    """Compact analytics block for dashboard context and briefing."""
    weekly = weekly_strength_rollups(strength_events, as_of=as_of, weeks=lookback_weeks)
    focus_weekly = weekly_focus_rollups(strength_events, as_of=as_of, weeks=lookback_weeks)
    exercises = top_exercises_summary(strength_events, as_of=as_of)
    flags = detect_strength_progress_flags(weekly)
    latest_week = weekly[-1] if weekly else {}
    prior_week = weekly[-2] if len(weekly) >= 2 else {}
    return {
        "as_of": as_of.isoformat(),
        "weekly_rollups": weekly,
        "focus_weekly": focus_weekly,
        "top_exercises": [
            {k: v for k, v in ex.items() if k != "series"} for ex in exercises
        ],
        "exercise_series": {ex["exercise_name"]: ex["series"] for ex in exercises},
        "this_week_volume_lbs": latest_week.get("volume_lbs"),
        "last_week_volume_lbs": prior_week.get("volume_lbs"),
        "week_over_week_change_pct": latest_week.get("change_pct"),
        "progress_flags": flags,
    }

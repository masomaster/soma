"""Body-weight trend from daily weigh-ins.

Windows are calendar days, not row counts, so a gap between scale readings
does not pretend to be a 7-day average. The briefing and dashboard narrate
this block; they do not recompute it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.features import as_date

# A phase target note such as "Lose about 5 lb by 2026-12-01".
_WEIGHT_GOAL_RE = re.compile(
    r"(?P<lbs>\d+(?:\.\d+)?)\s*lb\b[^.\n]{0,80}?\bby\s+(?P<day>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

_MIN_WEIGHT_LBS = 80.0
_MAX_WEIGHT_LBS = 400.0


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _round1(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 1)


def weight_points(
    history: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> list[tuple[date, float]]:
    """Sorted ``(date, lb)`` weigh-ins on or before ``as_of``. Later rows win a day."""
    by_date: dict[date, float] = {}
    for row in history:
        d = as_date(row.get("metric_date"))
        if d is None or d > as_of:
            continue
        value = _num(row.get("body_weight_lbs"))
        if value is None:
            continue
        by_date[d] = value
    return sorted(by_date.items())


def _mean_between(
    points: Sequence[tuple[date, float]],
    start: date,
    end: date,
) -> tuple[float | None, int]:
    values = [v for d, v in points if start <= d <= end]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)


def rolling_means(
    points: Sequence[tuple[date, float]],
    *,
    window_days: int,
) -> dict[date, float]:
    """Mean of weigh-ins in ``[d - window_days + 1, d]`` for each point date ``d``."""
    if window_days < 1:
        return {}
    out: dict[date, float] = {}
    for d, _value in points:
        mean, count = _mean_between(points, d - timedelta(days=window_days - 1), d)
        if mean is not None and count:
            out[d] = round(mean, 2)
    return out


def weight_goal_from_text(text: str | None) -> tuple[float | None, date | None]:
    """Parse a ``N lb by YYYY-MM-DD`` target from a phase note, if present."""
    if not isinstance(text, str) or not text:
        return None, None
    match = _WEIGHT_GOAL_RE.search(text)
    if match is None:
        return None, None
    try:
        goal_date = date.fromisoformat(match.group("day"))
    except ValueError:
        return None, None
    return float(match.group("lbs")), goal_date


def body_weight_log_row(
    *,
    user_id: str,
    metric_date: date,
    body_weight_lbs: float,
) -> dict[str, Any]:
    """Sparse ``daily_health_metrics`` row that records one weigh-in.

    Other columns for that day are left untouched by the sparse upsert.
    """
    weight = float(body_weight_lbs)
    if weight < _MIN_WEIGHT_LBS or weight > _MAX_WEIGHT_LBS:
        raise ValueError(
            f"body_weight_lbs must be between {_MIN_WEIGHT_LBS:.0f} and {_MAX_WEIGHT_LBS:.0f}"
        )
    return {
        "user_id": user_id,
        "metric_date": metric_date,
        "body_weight_lbs": round(weight, 2),
    }


def build_weight_trend(
    history: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    phase_start: date | None = None,
    target_loss_lbs: float | None = None,
    target_date: date | None = None,
) -> dict[str, Any]:
    """Latest weight, 7-day and 28-day means, and optional cut progress.

    ``vs_4wk_lbs`` is the recent 7-day mean (or the latest reading) minus the
    mean of weigh-ins in the 28 days ending ``as_of``. It is omitted when the
    28-day window has fewer than two readings. ``change_4w_lbs`` compares the
    last 7 days with the 7 days ending four weeks earlier.
    """
    points = weight_points(history, as_of=as_of)
    latest_date: date | None = None
    latest: float | None = None
    if points:
        latest_date, latest = points[-1]

    avg_7d, readings_7d = _mean_between(points, as_of - timedelta(days=6), as_of)
    avg_28d, readings_28d = _mean_between(points, as_of - timedelta(days=27), as_of)
    prior_7d, prior_n = _mean_between(
        points, as_of - timedelta(days=34), as_of - timedelta(days=28)
    )

    current = avg_7d if readings_7d else latest
    vs_4wk: float | None = None
    if current is not None and avg_28d is not None and readings_28d >= 2:
        vs_4wk = current - avg_28d
    change_4w: float | None = None
    if avg_7d is not None and prior_7d is not None and prior_n:
        change_4w = avg_7d - prior_7d

    phase_baseline_lbs: float | None = None
    phase_baseline_date: date | None = None
    change_since_phase: float | None = None
    if phase_start is not None and points:
        on_or_before = [(d, v) for d, v in points if d <= phase_start]
        in_phase = [(d, v) for d, v in points if d >= phase_start]
        if on_or_before:
            phase_baseline_date, phase_baseline_lbs = on_or_before[-1]
        elif in_phase:
            phase_baseline_date, phase_baseline_lbs = in_phase[0]
        if (
            phase_baseline_lbs is not None
            and phase_baseline_date is not None
            and latest is not None
            and latest_date is not None
            and latest_date > phase_baseline_date
        ):
            change_since_phase = latest - phase_baseline_lbs

    remaining: float | None = None
    if (
        target_loss_lbs is not None
        and phase_baseline_lbs is not None
        and latest is not None
        and change_since_phase is not None
    ):
        lost = phase_baseline_lbs - latest
        remaining = target_loss_lbs - lost

    days_since: int | None = None
    if latest_date is not None:
        days_since = (as_of - latest_date).days

    return {
        "as_of": as_of.isoformat(),
        "latest_lbs": _round1(latest),
        "latest_date": latest_date.isoformat() if latest_date else None,
        "days_since_weigh_in": days_since,
        "readings_7d": readings_7d,
        "avg_7d_lbs": _round1(avg_7d),
        "readings_28d": readings_28d,
        "avg_28d_lbs": _round1(avg_28d),
        "vs_4wk_lbs": _round1(vs_4wk),
        "change_4w_lbs": _round1(change_4w),
        "phase_start": phase_start.isoformat() if phase_start else None,
        "phase_baseline_lbs": _round1(phase_baseline_lbs),
        "phase_baseline_date": phase_baseline_date.isoformat() if phase_baseline_date else None,
        "change_since_phase_lbs": _round1(change_since_phase),
        "target_loss_lbs": target_loss_lbs,
        "target_date": target_date.isoformat() if target_date else None,
        "remaining_loss_lbs": _round1(remaining),
    }


def format_weight_glance(trend: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """Glance lines for the latest weigh-in and how it sits against 4 weeks."""
    if not trend or trend.get("latest_lbs") is None:
        return [("Body weight", "No weigh-in logged")]

    bits = [f"{float(trend['latest_lbs']):.1f} lb"]
    avg_7d = trend.get("avg_7d_lbs")
    readings_7d = trend.get("readings_7d") or 0
    if isinstance(avg_7d, (int, float)) and readings_7d >= 2:
        bits.append(f"7-day avg {float(avg_7d):.1f} lb")
    days_since = trend.get("days_since_weigh_in")
    if isinstance(days_since, int) and days_since >= 2 and trend.get("latest_date"):
        bits.append(f"last weighed {trend['latest_date']} ({days_since}d ago)")
    lines = [("Body weight", " · ".join(bits))]

    vs = trend.get("vs_4wk_lbs")
    avg_28d = trend.get("avg_28d_lbs")
    change_4w = trend.get("change_4w_lbs")
    if isinstance(vs, (int, float)) and isinstance(avg_28d, (int, float)):
        detail = f"{float(vs):+.1f} lb (4-wk avg {float(avg_28d):.1f} lb)"
        if isinstance(change_4w, (int, float)):
            detail += f" · {float(change_4w):+.1f} lb over 4 weeks"
        lines.append(("Weight vs 4-week avg", detail))
    elif (trend.get("readings_28d") or 0) < 2:
        lines.append(("Weight vs 4-week avg", "Need more daily weigh-ins"))

    target = trend.get("target_loss_lbs")
    target_date = trend.get("target_date")
    change = trend.get("change_since_phase_lbs")
    if isinstance(target, (int, float)) and isinstance(change, (int, float)):
        detail = f"{float(change):+.1f} lb since phase start · goal −{float(target):.0f} lb"
        remaining = trend.get("remaining_loss_lbs")
        if isinstance(remaining, (int, float)):
            detail += f" · {float(remaining):.1f} lb to go"
        if target_date:
            detail += f" by {target_date}"
        lines.append(("Cut progress", detail))
    elif isinstance(target, (int, float)) and target_date:
        phase_start = trend.get("phase_start")
        latest_date = trend.get("latest_date")
        started = (
            isinstance(phase_start, str)
            and isinstance(latest_date, str)
            and latest_date >= phase_start
        )
        if started:
            detail = (
                f"−{float(target):.0f} lb by {target_date} · "
                f"starting at {float(trend['latest_lbs']):.1f} lb"
            )
        else:
            detail = f"−{float(target):.0f} lb by {target_date} · waiting on a weigh-in this block"
        lines.append(("Cut goal", detail))
    return lines

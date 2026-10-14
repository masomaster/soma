"""Daily weight log: one calendar row per day, with a 7-day prior average.

The average on day D is the mean of weigh-ins in the 7 calendar days ending
on D (``[D-6, D]``). Days with no scale reading are skipped, not treated as
zero. Weekly change is that average minus the average from 7 days earlier
(the average on D minus the average on D-7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pipeline.features import as_date


def round1(value: float) -> float:
    """Round half up to one decimal, independent of binary float noise."""
    quantized = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return float(quantized.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:
            return None
        return number
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "--":
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _readings_by_day(
    history: Sequence[Mapping[str, Any]],
) -> tuple[dict[date, float], dict[date, float]]:
    """Latest weight and body-fat reading for each day. Later rows win."""
    weight: dict[date, float] = {}
    fat: dict[date, float] = {}
    for row in history:
        day = as_date(row.get("metric_date"))
        if day is None:
            continue
        pounds = _num(row.get("body_weight_lbs"))
        if pounds is not None:
            weight[day] = pounds
        pct = _num(row.get("body_fat_pct"))
        if pct is not None:
            fat[day] = pct
    return weight, fat


def _mean_between(weight: Mapping[date, float], start: date, end: date) -> float | None:
    values = [pounds for day, pounds in weight.items() if start <= day <= end]
    if not values:
        return None
    return sum(values) / len(values)


def daily_weight_log(
    history: Sequence[Mapping[str, Any]],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """One row per calendar day from the first reading through ``end``.

    ``avg_7d_lbs`` and ``weekly_change_lbs`` are rounded to one decimal.
    Weekly change is the displayed 7-day average minus the displayed average
    from seven days earlier, so the two columns subtract the way the sheet does.
    """
    weight, fat = _readings_by_day(history)
    if not weight and not fat:
        return []
    first = min([*weight.keys(), *fat.keys()])
    last = max([*weight.keys(), *fat.keys()])
    if start is not None:
        first = as_date(start) or first
    if end is not None:
        last = as_date(end) or last
    if first > last:
        return []

    rows: list[dict[str, Any]] = []
    day = first
    while day <= last:
        raw_avg = _mean_between(weight, day - timedelta(days=6), day)
        avg = round1(raw_avg) if raw_avg is not None else None
        prior_day = day - timedelta(days=7)
        raw_prior = _mean_between(weight, prior_day - timedelta(days=6), prior_day)
        prior = round1(raw_prior) if raw_prior is not None else None
        weekly = round1(avg - prior) if avg is not None and prior is not None else None
        rows.append(
            {
                "metric_date": day,
                "body_weight_lbs": weight.get(day),
                "body_fat_pct": fat.get(day),
                "avg_7d_lbs": avg,
                "weekly_change_lbs": weekly,
            }
        )
        day += timedelta(days=1)
    return rows


def weight_trend_line(points: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Least-squares line as two endpoints, first weigh-in through the last."""
    if len(points) < 2:
        return []
    xs = [float(day.toordinal()) for day, _pounds in points]
    ys = [pounds for _day, pounds in points]
    n = float(len(points))
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    var_x = sum((x - x_mean) ** 2 for x in xs)
    if var_x == 0:
        return []
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / var_x
    intercept = y_mean - slope * x_mean
    first, last = points[0][0], points[-1][0]

    def _y(day: date) -> float:
        return round1(intercept + slope * float(day.toordinal()))

    return [(first, _y(first)), (last, _y(last))]

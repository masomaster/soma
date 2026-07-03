"""Native Soma sleep score (0–100): a deterministic, pre-computed conclusion.

Fitbit's proprietary 0–100 sleep score **cannot** reach Soma — HealthKit has no
sleep-score data type, so no Apple Health / Health Auto Export bridge can carry
it (see ``docs/plans/fitbit-sleep-score.md``). Instead Soma computes its **own**
score from the sleep signals that *do* arrive (duration + stage hours via HAE,
plus resting HR and HRV). This lives in the feature/rollup layer, **not** an
adapter: adapters only normalize raw source data, while a derived score is a
*computed conclusion* the LLM later narrates (see ``.cursor/rules/soma.mdc``).

Design — a weighted blend of up to five components, each scored in ``[0, 1]``:

===========  ======  ==================================================
component    weight  meaning
===========  ======  ==================================================
duration     0.30    actual sleep vs personal need (default 8h)
stages       0.30    deep + REM fraction vs physiological optima
hrv          0.15    overnight HRV vs personal baseline (higher is better)
resting_hr   0.15    resting HR vs personal baseline (lower is better)
awake        0.10    wakefulness / interruptions (less is better)
===========  ======  ==================================================

Only components whose inputs are present contribute; the remaining weights are
**renormalized** over the available components, so a day with just a duration
still yields a score (it simply reflects fewer signals). The weighted mean is
scaled to 0–100 and clamped. Returns ``None`` when there is no sleep duration at
all — a sleep score with no sleep is meaningless.

Every mapping here is a transparent heuristic, not a validated clinical model:
baselines land near 0.75 (room to reward better-than-usual nights), and the
stage optima (deep ~18%, REM ~22% of total sleep) are mid-range population
figures. Tune the constants as real personal data accrues.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

DEFAULT_SLEEP_NEED_HOURS = 8.0

# Physiological mid-range stage fractions of total sleep time.
OPTIMAL_DEEP_FRACTION = 0.18
OPTIMAL_REM_FRACTION = 0.22

# Component weights (sum to 1.0 when every input is present).
WEIGHT_DURATION = 0.30
WEIGHT_STAGES = 0.30
WEIGHT_HRV = 0.15
WEIGHT_RESTING_HR = 0.15
WEIGHT_AWAKE = 0.10

# Trailing window used to derive personal HRV / resting-HR baselines.
BASELINE_WINDOW_DAYS = 28
# Fraction of the night awake at which the interruptions component hits zero.
AWAKE_ZERO_FRACTION = 0.15


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _duration_component(sleep_hours: float, need_hours: float) -> float:
    """Closeness of actual sleep to personal need; 1.0 at need, decaying either side."""
    if need_hours <= 0:
        return 0.0
    deviation = abs(sleep_hours - need_hours) / need_hours
    return _clamp(1.0 - deviation)


def _stage_component(sleep_hours: float, deep_hrs: float | None, rem_hrs: float | None) -> float | None:
    """Mean closeness of present stage fractions (deep / REM) to their optima."""
    stage_scores: list[float] = []
    for stage_hrs, optimal in ((deep_hrs, OPTIMAL_DEEP_FRACTION), (rem_hrs, OPTIMAL_REM_FRACTION)):
        if stage_hrs is None or stage_hrs < 0:
            continue
        fraction = stage_hrs / sleep_hours
        stage_scores.append(_clamp(1.0 - abs(fraction - optimal) / optimal))
    if not stage_scores:
        return None
    return sum(stage_scores) / len(stage_scores)


def _ratio_component(observed: float | None, baseline: float | None, *, higher_is_better: bool) -> float | None:
    """Score a metric vs baseline: baseline ≈ 0.75, ±deviations scaled by 1.5."""
    if observed is None or baseline is None or baseline <= 0:
        return None
    rel = (observed - baseline) / baseline
    if not higher_is_better:
        rel = -rel
    return _clamp(0.75 + rel * 1.5)


def _awake_component(awake_hours: float | None, sleep_hours: float) -> float | None:
    """Wakefulness penalty: 1.0 with no awake time, 0.0 at ``AWAKE_ZERO_FRACTION``."""
    if awake_hours is None or awake_hours < 0:
        return None
    total = sleep_hours + awake_hours
    if total <= 0:
        return None
    awake_fraction = awake_hours / total
    return _clamp(1.0 - awake_fraction / AWAKE_ZERO_FRACTION)


def compute_sleep_score(
    *,
    sleep_hours: float | None,
    sleep_deep_hrs: float | None = None,
    sleep_rem_hrs: float | None = None,
    resting_hr: float | None = None,
    hrv_rmssd: float | None = None,
    awake_hours: float | None = None,
    sleep_need_hours: float = DEFAULT_SLEEP_NEED_HOURS,
    hrv_baseline: float | None = None,
    resting_hr_baseline: float | None = None,
) -> float | None:
    """Compute a deterministic 0–100 sleep score, or ``None`` when no sleep is known.

    Components with missing inputs are dropped and the remaining weights are
    renormalized. HRV / resting-HR components require a personal ``*_baseline``;
    without one they simply do not contribute (they are not guessed).
    """
    hours = _num(sleep_hours)
    if hours is None or hours <= 0:
        return None

    weighted: list[tuple[float, float]] = [
        (WEIGHT_DURATION, _duration_component(hours, sleep_need_hours)),
    ]

    def _add(weight: float, value: float | None) -> None:
        if value is not None:
            weighted.append((weight, value))

    _add(WEIGHT_STAGES, _stage_component(hours, _num(sleep_deep_hrs), _num(sleep_rem_hrs)))
    _add(WEIGHT_HRV, _ratio_component(_num(hrv_rmssd), _num(hrv_baseline), higher_is_better=True))
    _add(
        WEIGHT_RESTING_HR,
        _ratio_component(_num(resting_hr), _num(resting_hr_baseline), higher_is_better=False),
    )
    _add(WEIGHT_AWAKE, _awake_component(_num(awake_hours), hours))

    total_weight = sum(w for w, _ in weighted)
    if total_weight <= 0:
        return None
    score = sum(w * v for w, v in weighted) / total_weight * 100.0
    return round(_clamp(score, 0.0, 100.0), 1)


def trailing_baseline(
    daily_metrics: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    as_of: date,
    days: int = BASELINE_WINDOW_DAYS,
    min_samples: int = 2,
) -> float | None:
    """Mean of ``metric`` over the ``days`` days strictly before ``as_of``.

    Returns ``None`` when fewer than ``min_samples`` observations exist, so a
    sparse history never produces a misleading baseline. ``metric_date`` values
    may be ``date`` / ``datetime`` / ISO strings (coerced via a local prefix parse).
    """
    start = as_of - timedelta(days=days)
    values: list[float] = []
    for row in daily_metrics:
        d = _as_date(row.get("metric_date"))
        if d is None or d < start or d >= as_of:
            continue
        v = _num(row.get(metric))
        if v is not None:
            values.append(v)
    if len(values) < min_samples:
        return None
    return sum(values) / len(values)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None

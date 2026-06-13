"""Phase 6 feature computation: biometrics rollup + deterministic ``daily_features``.

All functions here are **pure** (no DB / network): callers pass already-loaded
rows (the shapes produced by adapters / ``SELECT``) and receive plain dicts ready
to upsert into ``daily_health_metrics`` / ``daily_features``. The LLM never sees
raw events — it only narrates the conclusions computed here (see
``.cursor/rules/soma.mdc``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

# Canonical biometric metric names that map 1:1 onto ``daily_health_metrics``
# columns (see schema/migrations/0001_initial.sql). Anything else is ignored by
# the rollup so unknown vendor metrics never silently land in the wide table.
DAILY_HEALTH_METRIC_COLUMNS: frozenset[str] = frozenset(
    {
        "hrv_rmssd",
        "resting_hr",
        "spo2_pct",
        "respiratory_rate",
        "sleep_hours",
        "sleep_deep_hrs",
        "sleep_rem_hrs",
        "sleep_score",
        "steps",
        "active_cal",
        "vo2_max",
        "body_weight_lbs",
        "body_fat_pct",
        "muscle_mass_lbs",
    }
)

ACUTE_WINDOW_DAYS = 7
CHRONIC_WINDOW_DAYS = 28
# Sets Hevy marks as real working effort (see hevy._hevy_set_type_to_db).
_HARD_SET_TYPES = frozenset({"working"})


def _as_date(value: Any) -> date | None:
    """Coerce a ``date`` / ISO ``YYYY-MM-DD`` (date-or-datetime) string to ``date``."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def as_date(value: Any) -> date | None:
    """Public coercion to ``date`` (``date`` or ISO ``YYYY-MM-DD[...]`` string)."""
    return _as_date(value)


def _in_window(event_date: date, *, as_of: date, days: int) -> bool:
    """True if ``event_date`` falls in the inclusive ``[as_of - (days-1), as_of]`` window."""
    delta = (as_of - event_date).days
    return 0 <= delta < days


def _num(value: Any) -> float | None:
    """Return a float for numeric (non-bool) values, else ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def rollup_daily_health_metrics(
    biometric_rows: Iterable[Mapping[str, Any]],
    *,
    user_id: str,
    metric_date: date,
) -> dict[str, Any]:
    """Pivot canonical ``biometrics`` EAV rows for one day into a wide row dict.

    Each input row is shaped like the ``biometrics`` table / adapter output:
    ``{"metric": "hrv_rmssd", "value": 48.2, ...}``. Only metrics in
    :data:`DAILY_HEALTH_METRIC_COLUMNS` are kept; the last value wins on
    duplicates. The returned dict is keyed by ``(user_id, metric_date)`` plus
    whatever metrics were present (sparse — missing columns are simply absent).
    """
    row: dict[str, Any] = {"user_id": user_id, "metric_date": metric_date}
    for entry in biometric_rows:
        metric = entry.get("metric")
        if not isinstance(metric, str) or metric not in DAILY_HEALTH_METRIC_COLUMNS:
            continue
        value = _num(entry.get("value"))
        if value is None:
            continue
        # Integer-typed columns in the schema keep clean ints.
        if metric in {"resting_hr", "steps", "active_cal"}:
            row[metric] = int(round(value))
        else:
            row[metric] = value
    return row


def _strength_features(
    strength_events: Sequence[Mapping[str, Any]], *, as_of: date
) -> dict[str, Any]:
    session_dates: set[date] = set()
    hard_sets = 0
    tonnage = 0.0
    for ev in strength_events:
        d = _as_date(ev.get("event_date"))
        if d is None or not _in_window(d, as_of=as_of, days=ACUTE_WINDOW_DAYS):
            continue
        session_dates.add(d)
        set_type = str(ev.get("set_type") or "").strip().lower()
        if set_type in _HARD_SET_TYPES:
            hard_sets += 1
            reps = _num(ev.get("reps"))
            weight = _num(ev.get("weight_lbs"))
            if reps is not None and weight is not None:
                tonnage += reps * weight
    return {
        "strength_sessions_7d": len(session_dates),
        "strength_hard_sets_7d": hard_sets,
        "strength_tonnage_7d": round(tonnage, 2),
    }


def _cardio_features(
    cardio_events: Sequence[Mapping[str, Any]], *, as_of: date
) -> dict[str, Any]:
    sessions_7d: set[date] = set()
    minutes_7d = 0.0
    minutes_14d = 0.0
    minutes_acute = 0.0
    minutes_chronic = 0.0
    for ev in cardio_events:
        d = _as_date(ev.get("event_date"))
        if d is None:
            continue
        minutes = _num(ev.get("duration_min")) or 0.0
        if _in_window(d, as_of=as_of, days=ACUTE_WINDOW_DAYS):
            sessions_7d.add(d)
            minutes_7d += minutes
            minutes_acute += minutes
        if _in_window(d, as_of=as_of, days=14):
            minutes_14d += minutes
        if _in_window(d, as_of=as_of, days=CHRONIC_WINDOW_DAYS):
            minutes_chronic += minutes
    # Acute:chronic workload ratio (ACWR): 7-day load vs the 28-day weekly average.
    chronic_weekly_avg = minutes_chronic / (CHRONIC_WINDOW_DAYS / ACUTE_WINDOW_DAYS)
    acwr = round(minutes_acute / chronic_weekly_avg, 3) if chronic_weekly_avg > 0 else None
    return {
        "cardio_sessions_7d": len(sessions_7d),
        "cardio_minutes_7d": round(minutes_7d, 2),
        "cardio_minutes_14d": round(minutes_14d, 2),
        "acute_chronic_ratio": acwr,
    }


def _recovery_features(
    daily_metrics: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    target_sleep_hours: float,
    hrv_suppressed_ratio: float,
) -> dict[str, Any]:
    """Sleep debt + HRV suppression over the acute window from wide daily metrics."""
    window = [
        m
        for m in daily_metrics
        if (d := _as_date(m.get("metric_date"))) is not None
        and _in_window(d, as_of=as_of, days=ACUTE_WINDOW_DAYS)
    ]
    hrv_values = [v for m in window if (v := _num(m.get("hrv_rmssd"))) is not None]
    hrv_baseline = sum(hrv_values) / len(hrv_values) if hrv_values else None

    sleep_debt = 0.0
    suppressed_days = 0
    for m in window:
        sleep = _num(m.get("sleep_hours"))
        if sleep is not None:
            sleep_debt += max(0.0, target_sleep_hours - sleep)
        hrv = _num(m.get("hrv_rmssd"))
        if hrv is not None and hrv_baseline is not None and hrv < hrv_baseline * hrv_suppressed_ratio:
            suppressed_days += 1
    return {
        "sleep_debt_7d": round(sleep_debt, 2),
        "hrv_suppressed_days": suppressed_days,
    }


def _readiness_score(features: Mapping[str, Any], *, target_sleep_hours: float) -> float:
    """Composite 0–100 readiness: starts at 100, subtract penalties.

    Deterministic and intentionally simple (the rules engine and LLM add nuance):
    - sleep debt: −4 points per cumulative hour short over the week (cap −40)
    - HRV suppression: −8 points per suppressed day (cap −40)
    - training-load spike: −20 if ACWR > 1.5 (classic injury-risk threshold)
    """
    score = 100.0
    sleep_debt = _num(features.get("sleep_debt_7d")) or 0.0
    score -= min(40.0, sleep_debt * 4.0)
    suppressed = _num(features.get("hrv_suppressed_days")) or 0.0
    score -= min(40.0, suppressed * 8.0)
    acwr = _num(features.get("acute_chronic_ratio"))
    if acwr is not None and acwr > 1.5:
        score -= 20.0
    return round(max(0.0, min(100.0, score)), 1)


def compute_daily_features(
    *,
    user_id: str,
    feature_date: date,
    strength_events: Sequence[Mapping[str, Any]] = (),
    cardio_events: Sequence[Mapping[str, Any]] = (),
    daily_metrics: Sequence[Mapping[str, Any]] = (),
    target_sleep_hours: float = 8.0,
    hrv_suppressed_ratio: float = 0.85,
) -> dict[str, Any]:
    """Compute a deterministic ``daily_features`` row from windowed event/metric rows.

    ``daily_metrics`` are wide rows (e.g. from :func:`rollup_daily_health_metrics`
    or a ``daily_health_metrics`` ``SELECT``) covering at least the trailing
    :data:`CHRONIC_WINDOW_DAYS`. Fields that need data not yet ingested (muscle
    group / movement splits) are left unset so the column stays ``NULL``.
    """
    features: dict[str, Any] = {"user_id": user_id, "feature_date": feature_date}
    features.update(_strength_features(strength_events, as_of=feature_date))
    features.update(_cardio_features(cardio_events, as_of=feature_date))
    features.update(
        _recovery_features(
            daily_metrics,
            as_of=feature_date,
            target_sleep_hours=target_sleep_hours,
            hrv_suppressed_ratio=hrv_suppressed_ratio,
        )
    )
    features["overall_readiness_score"] = _readiness_score(
        features, target_sleep_hours=target_sleep_hours
    )
    return features

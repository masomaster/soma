"""Phase 6 feature computation: biometrics rollup + deterministic ``daily_features``.

All functions here are **pure** (no DB / network): callers pass already-loaded
rows (the shapes produced by adapters / ``SELECT``) and receive plain dicts ready
to upsert into ``daily_health_metrics`` / ``daily_features``. The LLM never sees
raw events — it only narrates the conclusions computed here (see
``.cursor/rules/soma.mdc``). Strength ``strength_tonnage_7d`` is **US short tons**
(``sum(reps * weight_lbs) / 2000``) over the acute window.

**Training load (v0):** Modality-split external metrics under ``training_load_*``
mirror 7d legacy columns and add **28-day** rolling strength tonnage / cardio
minutes — see ``docs/plans/workload-indicators.md``.

**Effort (v1 attempt):** ``effort_unified_index_*`` is a **heuristic** single scale
(cardio minutes + strength short tons × a nominal conversion). Foster-style
``effort_foster_*`` uses ``cardio_events.session_rpe`` and per-set ``rpe`` on
strength working sets when present — not a validated physiological TRIMP.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from pipeline.cardio_quality import (
    DEFAULT_RUN_PACE_MAX_SEC_MI,
    DEFAULT_RUN_PACE_MIN_SEC_MI,
    assess_cardio_quality,
)

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
# Hevy stores ``weight_lbs``; convert summed (reps × lbs) into approximate US short tons.
LBS_PER_SHORT_TON = 2000.0
# Sets Hevy marks as real working effort (see hevy._hevy_set_type_to_db).
_HARD_SET_TYPES = frozenset({"working"})
# Heuristic unified index: map strength short tons to nominal cardio-equivalent minutes
# (arbitrary units for trending — not VO₂-derived). See workload-indicators.md.
EFFORT_STRENGTH_SHORT_TON_AS_EQUIV_CARDIO_MINUTES = 90.0
# Foster-style strength: minutes proxy per hard set when inferring session duration from sets only.
EFFORT_STRENGTH_MINUTES_PER_HARD_SET = 3.0


def _as_date(value: Any) -> date | None:
    """Coerce ``datetime``, plain ``date``, or ISO ``YYYY-MM-DD[...]`` string to ``date``."""
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


def as_date(value: Any) -> date | None:
    """Public coercion to calendar ``date`` (``datetime``, ``date``, or ISO string prefix)."""
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


def _strength_training_load(
    strength_events: Sequence[Mapping[str, Any]], *, as_of: date
) -> dict[str, Any]:
    """Legacy 7d strength metrics plus modality-split ``training_load_strength_*`` (7d + 28d)."""
    session_7: set[date] = set()
    session_28: set[date] = set()
    hard_7 = 0
    hard_28 = 0
    vol_7_lb = 0.0
    vol_28_lb = 0.0
    for ev in strength_events:
        d = _as_date(ev.get("event_date"))
        if d is None:
            continue
        in7 = _in_window(d, as_of=as_of, days=ACUTE_WINDOW_DAYS)
        in28 = _in_window(d, as_of=as_of, days=CHRONIC_WINDOW_DAYS)
        if not in28:
            continue
        if in7:
            session_7.add(d)
        session_28.add(d)
        set_type = str(ev.get("set_type") or "").strip().lower()
        if set_type not in _HARD_SET_TYPES:
            continue
        reps = _num(ev.get("reps"))
        weight = _num(ev.get("weight_lbs"))
        if in7:
            hard_7 += 1
            if reps is not None and weight is not None:
                vol_7_lb += reps * weight
        hard_28 += 1
        if reps is not None and weight is not None:
            vol_28_lb += reps * weight
    tons_7 = round(vol_7_lb / LBS_PER_SHORT_TON, 3)
    tons_28 = round(vol_28_lb / LBS_PER_SHORT_TON, 3)
    return {
        "strength_sessions_7d": len(session_7),
        "strength_hard_sets_7d": hard_7,
        "strength_tonnage_7d": tons_7,
        "training_load_strength_short_tons_7d": tons_7,
        "training_load_strength_short_tons_28d": tons_28,
        "training_load_strength_hard_sets_28d": hard_28,
        "training_load_strength_sessions_28d": len(session_28),
    }


def _cardio_training_load(
    cardio_events: Sequence[Mapping[str, Any]], *, as_of: date
) -> dict[str, Any]:
    """Legacy cardio windows plus ``training_load_cardio_minutes_*`` (7d + 28d)."""
    sessions_7d: set[date] = set()
    minutes_7d = 0.0
    minutes_14d = 0.0
    minutes_acute = 0.0
    minutes_chronic = 0.0
    minutes_28d = 0.0
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
            minutes_28d += minutes
    chronic_weekly_avg = minutes_chronic / (CHRONIC_WINDOW_DAYS / ACUTE_WINDOW_DAYS)
    acwr = round(minutes_acute / chronic_weekly_avg, 3) if chronic_weekly_avg > 0 else None
    return {
        "cardio_sessions_7d": len(sessions_7d),
        "cardio_minutes_7d": round(minutes_7d, 2),
        "cardio_minutes_14d": round(minutes_14d, 2),
        "acute_chronic_ratio": acwr,
        "training_load_cardio_minutes_7d": round(minutes_7d, 2),
        "training_load_cardio_minutes_28d": round(minutes_28d, 2),
    }


def _cardio_quality_features(
    cardio_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    run_pace_min_sec_mi: float,
    run_pace_max_sec_mi: float,
) -> dict[str, Any]:
    """Count 7d cardio sessions whose recorded distance is physically implausible.

    Re-tags from the row's own duration/distance (not a stored flag) so an SSM
    band change takes effect without re-ingesting. Feeds the briefing's
    data-quality note; the session still counts elsewhere for frequency/duration.
    """
    suspect = 0
    for ev in cardio_events:
        d = _as_date(ev.get("event_date"))
        if d is None or not _in_window(d, as_of=as_of, days=ACUTE_WINDOW_DAYS):
            continue
        if assess_cardio_quality(
            ev,
            run_pace_min_sec_mi=run_pace_min_sec_mi,
            run_pace_max_sec_mi=run_pace_max_sec_mi,
        ):
            suspect += 1
    return {"cardio_distance_suspect_7d": suspect}


def _effort_foster_strength_au(
    strength_events: Sequence[Mapping[str, Any]], *, as_of: date, days: int
) -> float | None:
    """Foster-style AU from strength: per calendar day, mean(set RPE) × minutes proxy for hard sets."""
    day_rpes: dict[date, list[float | None]] = defaultdict(list)
    for ev in strength_events:
        d = _as_date(ev.get("event_date"))
        if d is None or not _in_window(d, as_of=as_of, days=days):
            continue
        if str(ev.get("set_type") or "").strip().lower() not in _HARD_SET_TYPES:
            continue
        day_rpes[d].append(_num(ev.get("rpe")))

    total = 0.0
    contributed = False
    for rpes in day_rpes.values():
        valid = [x for x in rpes if x is not None]
        if not valid:
            continue
        n_sets = len(rpes)
        mean_rpe = sum(valid) / len(valid)
        total += mean_rpe * (n_sets * EFFORT_STRENGTH_MINUTES_PER_HARD_SET)
        contributed = True
    return round(total, 2) if contributed else None


def _effort_foster_cardio_au(
    cardio_events: Sequence[Mapping[str, Any]], *, as_of: date, days: int
) -> float | None:
    """Foster AU: sum of ``duration_min * session_rpe`` when both are set on a cardio row."""
    total = 0.0
    for ev in cardio_events:
        d = _as_date(ev.get("event_date"))
        if d is None or not _in_window(d, as_of=as_of, days=days):
            continue
        rpe = _num(ev.get("session_rpe"))
        dur = _num(ev.get("duration_min"))
        if rpe is None or dur is None or dur <= 0:
            continue
        total += rpe * dur
    return round(total, 2) if total > 0 else None


def _effort_foster_combined(
    cardio_au: float | None, strength_au: float | None
) -> float | None:
    if cardio_au is None and strength_au is None:
        return None
    return round((cardio_au or 0.0) + (strength_au or 0.0), 2)


def _effort_features(
    strength_events: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    training_load_cardio_minutes_7d: float,
    training_load_cardio_minutes_28d: float,
    training_load_strength_short_tons_7d: float,
    training_load_strength_short_tons_28d: float,
) -> dict[str, Any]:
    """Unified effort index (heuristic) + optional Foster RPE-derived AU."""
    fc7 = _effort_foster_cardio_au(cardio_events, as_of=as_of, days=ACUTE_WINDOW_DAYS)
    fs7 = _effort_foster_strength_au(strength_events, as_of=as_of, days=ACUTE_WINDOW_DAYS)
    fc28 = _effort_foster_cardio_au(cardio_events, as_of=as_of, days=CHRONIC_WINDOW_DAYS)
    fs28 = _effort_foster_strength_au(strength_events, as_of=as_of, days=CHRONIC_WINDOW_DAYS)
    k = EFFORT_STRENGTH_SHORT_TON_AS_EQUIV_CARDIO_MINUTES
    idx7 = training_load_cardio_minutes_7d + training_load_strength_short_tons_7d * k
    idx28 = training_load_cardio_minutes_28d + training_load_strength_short_tons_28d * k
    return {
        "effort_unified_index_7d": round(idx7, 2),
        "effort_unified_index_28d": round(idx28, 2),
        "effort_foster_cardio_au_7d": fc7,
        "effort_foster_strength_au_7d": fs7,
        "effort_foster_au_7d": _effort_foster_combined(fc7, fs7),
        "effort_foster_cardio_au_28d": fc28,
        "effort_foster_strength_au_28d": fs28,
        "effort_foster_au_28d": _effort_foster_combined(fc28, fs28),
    }


def _acute_calendar_dates(as_of: date, *, days: int = ACUTE_WINDOW_DAYS) -> list[date]:
    """Inclusive trailing ``days`` calendar days ending at ``as_of``."""
    return [as_of - timedelta(days=i) for i in range(days)]


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
    sleep_obs_days = 0
    hrv_obs_days = 0
    for d in _acute_calendar_dates(as_of):
        day_rows = [m for m in window if _as_date(m.get("metric_date")) == d]
        if any(_num(m.get("sleep_hours")) is not None for m in day_rows):
            sleep_obs_days += 1
        if any(_num(m.get("hrv_rmssd")) is not None for m in day_rows):
            hrv_obs_days += 1

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
        "recovery_sleep_days_7d": sleep_obs_days,
        "recovery_hrv_days_7d": hrv_obs_days,
        "sleep_debt_7d": None if sleep_obs_days == 0 else round(sleep_debt, 2),
        "hrv_suppressed_days": suppressed_days,
    }


def _readiness_score(
    features: Mapping[str, Any],
    *,
    target_sleep_hours: float,
    max_acute_chronic_ratio: float,
) -> float:
    """Composite 0–100 readiness: starts at 100, subtract penalties.

    Deterministic and intentionally simple (the rules engine and LLM add nuance):
    - sleep debt: −4 points per cumulative hour short over the week (cap −40)
    - HRV suppression: −8 points per suppressed day (cap −40)
    - training-load spike: −20 when ACWR exceeds ``max_acute_chronic_ratio``
      (same configurable threshold the rules engine flags on)
    """
    score = 100.0
    sleep_debt = _num(features.get("sleep_debt_7d"))
    if sleep_debt is not None:
        score -= min(40.0, sleep_debt * 4.0)
    suppressed = _num(features.get("hrv_suppressed_days")) or 0.0
    score -= min(40.0, suppressed * 8.0)
    acwr = _num(features.get("acute_chronic_ratio"))
    if acwr is not None and acwr > max_acute_chronic_ratio:
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
    max_acute_chronic_ratio: float = 1.5,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
    run_pace_max_sec_mi: float = DEFAULT_RUN_PACE_MAX_SEC_MI,
) -> dict[str, Any]:
    """Compute a deterministic ``daily_features`` row from windowed event/metric rows.

    ``daily_metrics`` are wide rows (e.g. from :func:`rollup_daily_health_metrics`
    or a ``daily_health_metrics`` ``SELECT``) covering at least the trailing
    :data:`CHRONIC_WINDOW_DAYS`. Fields that need data not yet ingested (muscle
    group / movement splits) are left unset so the column stays ``NULL``.
    """
    features: dict[str, Any] = {"user_id": user_id, "feature_date": feature_date}
    features.update(_strength_training_load(strength_events, as_of=feature_date))
    features.update(_cardio_training_load(cardio_events, as_of=feature_date))
    features.update(
        _cardio_quality_features(
            cardio_events,
            as_of=feature_date,
            run_pace_min_sec_mi=run_pace_min_sec_mi,
            run_pace_max_sec_mi=run_pace_max_sec_mi,
        )
    )
    features.update(
        _effort_features(
            strength_events,
            cardio_events,
            as_of=feature_date,
            training_load_cardio_minutes_7d=float(
                features.get("training_load_cardio_minutes_7d") or 0.0
            ),
            training_load_cardio_minutes_28d=float(
                features.get("training_load_cardio_minutes_28d") or 0.0
            ),
            training_load_strength_short_tons_7d=float(
                features.get("training_load_strength_short_tons_7d") or 0.0
            ),
            training_load_strength_short_tons_28d=float(
                features.get("training_load_strength_short_tons_28d") or 0.0
            ),
        )
    )
    features.update(
        _recovery_features(
            daily_metrics,
            as_of=feature_date,
            target_sleep_hours=target_sleep_hours,
            hrv_suppressed_ratio=hrv_suppressed_ratio,
        )
    )
    sleep_cov = int(features.get("recovery_sleep_days_7d") or 0)
    hrv_cov = int(features.get("recovery_hrv_days_7d") or 0)
    if sleep_cov == 0 and hrv_cov == 0:
        features["overall_readiness_score"] = None
    else:
        features["overall_readiness_score"] = _readiness_score(
            features,
            target_sleep_hours=target_sleep_hours,
            max_acute_chronic_ratio=max_acute_chronic_ratio,
        )
    return features

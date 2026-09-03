"""Recompute ``daily_features`` (readiness, sleep debt, ACWR, load) without briefing.

The dashboard reads ``daily_features.overall_readiness_score``. That used to be
written only by the morning briefing Lambda — often with empty recovery windows
because ``daily_health_metrics`` had not been rolled up yet. After biometrics
(and cardio) land, callers should invoke :func:`recompute_daily_features` so
readiness stays current independently of email generation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pipeline.features import (
    ACUTE_WINDOW_DAYS,
    CHRONIC_WINDOW_DAYS,
    as_date,
    compute_daily_features,
)
from pipeline.persistence import upsert_row
from pipeline.rules import DEFAULT_THRESHOLDS

logger = logging.getLogger(__name__)

# Cardio/strength chronic window matches clients.build_db_loaders lookback.
_EVENT_LOOKBACK_DAYS = CHRONIC_WINDOW_DAYS


def feature_dates_affected_by_metric_days(
    metric_dates: Sequence[date],
    *,
    through: date | None = None,
) -> list[date]:
    """Feature days whose 7d recovery window includes any of ``metric_dates``.

    Changing sleep/HRV on day ``D`` affects readiness for ``D`` .. ``D+6``.
    """
    cap = through or datetime.now(timezone.utc).date()
    out: set[date] = set()
    for d in metric_dates:
        if not isinstance(d, date):
            continue
        for offset in range(ACUTE_WINDOW_DAYS):
            f = d + timedelta(days=offset)
            if f <= cap:
                out.add(f)
    return sorted(out)


def feature_dates_affected_by_activity_days(
    activity_dates: Sequence[date],
    *,
    through: date | None = None,
) -> list[date]:
    """Feature days whose 28d load window includes any of ``activity_dates``."""
    cap = through or datetime.now(timezone.utc).date()
    out: set[date] = set()
    for d in activity_dates:
        if not isinstance(d, date):
            continue
        for offset in range(CHRONIC_WINDOW_DAYS):
            f = d + timedelta(days=offset)
            if f <= cap:
                out.add(f)
    return sorted(out)


def _rows_as_dicts(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _load_metrics_window(
    cur: Any, *, user_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT * FROM daily_health_metrics "
        "WHERE user_id = %s AND metric_date BETWEEN %s AND %s "
        "ORDER BY metric_date",
        (user_id, start, end),
    )
    return _rows_as_dicts(cur)


def _load_strength_window(
    cur: Any, *, user_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT event_date, exercise_name, set_type, reps, weight_lbs, rpe "
        "FROM strength_events "
        "WHERE user_id = %s AND event_date BETWEEN %s AND %s",
        (user_id, start, end),
    )
    return _rows_as_dicts(cur)


def _load_cardio_window(
    cur: Any, *, user_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT event_date, activity_type, distance_miles, duration_min, session_rpe "
        "FROM cardio_events "
        "WHERE user_id = %s AND event_date BETWEEN %s AND %s",
        (user_id, start, end),
    )
    return _rows_as_dicts(cur)


def list_daily_health_metric_dates(
    cur: Any,
    *,
    user_id: str,
    since: date | None = None,
    until: date | None = None,
) -> list[date]:
    """Distinct ``metric_date`` values in ``daily_health_metrics`` (optional range)."""
    clauses = ["user_id = %s"]
    params: list[Any] = [user_id]
    if since is not None:
        clauses.append("metric_date >= %s")
        params.append(since)
    if until is not None:
        clauses.append("metric_date <= %s")
        params.append(until)
    cur.execute(
        f"SELECT DISTINCT metric_date FROM daily_health_metrics "
        f"WHERE {' AND '.join(clauses)} ORDER BY metric_date",
        tuple(params),
    )
    return [r[0] for r in cur.fetchall() if isinstance(r[0], date)]


def recompute_daily_features(
    cur: Any,
    *,
    user_id: str,
    dates: Sequence[date],
    thresholds: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Compute and upsert ``daily_features`` for each date (chronological).

    Loads strength / cardio / health-metric windows once for the whole span.
    Always persists the row (including null readiness when recovery coverage is
    zero) so charts stay aligned with the latest training-load fields.
    """
    unique = sorted({d for d in dates if isinstance(d, date)})
    if not unique:
        return []

    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    lo, hi = unique[0], unique[-1]
    lookback_start = lo - timedelta(days=max(_EVENT_LOOKBACK_DAYS, CHRONIC_WINDOW_DAYS) - 1)

    metrics = _load_metrics_window(cur, user_id=user_id, start=lookback_start, end=hi)
    strength = _load_strength_window(cur, user_id=user_id, start=lookback_start, end=hi)
    cardio = _load_cardio_window(cur, user_id=user_id, start=lookback_start, end=hi)

    upserted: list[dict[str, Any]] = []
    for d in unique:
        row = compute_daily_features(
            user_id=user_id,
            feature_date=d,
            strength_events=strength,
            cardio_events=cardio,
            daily_metrics=metrics,
            target_sleep_hours=float(th["target_sleep_hours"]),
            hrv_suppressed_ratio=float(th["hrv_suppressed_ratio"]),
            max_acute_chronic_ratio=float(th["max_acute_chronic_ratio"]),
            run_pace_min_sec_mi=float(th["cardio_run_pace_min_sec_mi"]),
            run_pace_max_sec_mi=float(th["cardio_run_pace_max_sec_mi"]),
        )
        upsert_row(cur, "daily_features", row)
        upserted.append(row)

    if upserted:
        logger.info(
            "Recomputed %d daily_features day(s) for user %s (%s .. %s); "
            "readiness_non_null=%d",
            len(upserted),
            user_id,
            upserted[0]["feature_date"],
            upserted[-1]["feature_date"],
            sum(1 for r in upserted if r.get("overall_readiness_score") is not None),
        )
    return upserted


def event_dates_from_rows(
    rows: Sequence[Mapping[str, Any]], *, date_key: str = "event_date"
) -> list[date]:
    """Unique calendar dates from cardio/strength-style row dicts."""
    out: set[date] = set()
    for row in rows:
        d = as_date(row.get(date_key))
        if d is not None:
            out.add(d)
    return sorted(out)

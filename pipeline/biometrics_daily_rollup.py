"""Promote ``biometrics`` EAV rows into wide ``daily_health_metrics``.

The dashboard and coaching surfaces read ``daily_health_metrics``, not raw
``biometrics``. Ingest paths (Apple Health webhook, local smoke) must call this
after upserting biometrics so charts stay current **without** waiting for the
daily briefing Lambda.

The briefing pipeline may still roll up ``run_date`` for its own feature
window; that is secondary. Empty shells (only ``user_id`` / ``metric_date``)
are **not** written here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.features import as_date, rollup_daily_health_metrics
from pipeline.persistence import upsert_row
from pipeline.sleep_score import DEFAULT_SLEEP_NEED_HOURS, trailing_baseline

logger = logging.getLogger(__name__)

# How far back to look for HRV / resting-HR baselines when scoring sleep.
_BASELINE_LOOKBACK_DAYS = 28


def event_dates_from_biometrics_rows(rows: Iterable[dict[str, Any]]) -> list[date]:
    """Unique calendar dates present on normalized biometrics rows (sorted)."""
    dates: set[date] = set()
    for row in rows:
        d = as_date(row.get("event_date"))
        if d is not None:
            dates.add(d)
    return sorted(dates)


def _load_biometrics_for_date(cur: Any, *, user_id: str, event_date: date) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT metric, value FROM biometrics "
        "WHERE user_id = %s AND event_date = %s",
        (user_id, event_date),
    )
    return [{"metric": r[0], "value": r[1]} for r in cur.fetchall()]


def _load_metrics_window_before(
    cur: Any, *, user_id: str, as_of: date, days: int = _BASELINE_LOOKBACK_DAYS
) -> list[dict[str, Any]]:
    start = as_of - timedelta(days=days)
    cur.execute(
        "SELECT metric_date, hrv_rmssd, resting_hr FROM daily_health_metrics "
        "WHERE user_id = %s AND metric_date >= %s AND metric_date < %s "
        "ORDER BY metric_date",
        (user_id, start, as_of),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _wide_row_has_metrics(row: dict[str, Any]) -> bool:
    return any(k not in {"user_id", "metric_date"} for k in row)


def rollup_biometrics_dates(
    cur: Any,
    *,
    user_id: str,
    dates: Sequence[date],
    sleep_need_hours: float = DEFAULT_SLEEP_NEED_HOURS,
) -> list[dict[str, Any]]:
    """Load biometrics for each date, roll up, upsert ``daily_health_metrics``.

    Dates are processed in chronological order so each day's sleep score can use
    baselines already written for earlier days in the same batch. Skips dates
    with no biometric samples (does not insert empty shells).

    Returns the wide rows that were upserted.
    """
    if not dates:
        return []

    unique = sorted({d for d in dates if isinstance(d, date)})
    upserted: list[dict[str, Any]] = []

    for d in unique:
        bio = _load_biometrics_for_date(cur, user_id=user_id, event_date=d)
        if not bio:
            continue
        window = _load_metrics_window_before(cur, user_id=user_id, as_of=d)
        # Prefer freshly rolled rows from this batch over stale DB nulls.
        window.extend(upserted)
        wide = rollup_daily_health_metrics(
            bio,
            user_id=user_id,
            metric_date=d,
            sleep_need_hours=sleep_need_hours,
            hrv_baseline=trailing_baseline(window, metric="hrv_rmssd", as_of=d),
            resting_hr_baseline=trailing_baseline(window, metric="resting_hr", as_of=d),
        )
        if not _wide_row_has_metrics(wide):
            continue
        upsert_row(cur, "daily_health_metrics", wide)
        upserted.append(wide)

    if upserted:
        logger.info(
            "Rolled up %d daily_health_metrics day(s) for user %s (%s .. %s)",
            len(upserted),
            user_id,
            upserted[0]["metric_date"],
            upserted[-1]["metric_date"],
        )
    return upserted


def list_biometrics_event_dates(
    cur: Any,
    *,
    user_id: str,
    since: date | None = None,
    until: date | None = None,
) -> list[date]:
    """Distinct ``event_date`` values in ``biometrics`` for a user (optional range)."""
    clauses = ["user_id = %s"]
    params: list[Any] = [user_id]
    if since is not None:
        clauses.append("event_date >= %s")
        params.append(since)
    if until is not None:
        clauses.append("event_date <= %s")
        params.append(until)
    cur.execute(
        f"SELECT DISTINCT event_date FROM biometrics WHERE {' AND '.join(clauses)} "
        "ORDER BY event_date",
        tuple(params),
    )
    return [r[0] for r in cur.fetchall() if isinstance(r[0], date)]

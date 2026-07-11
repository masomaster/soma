"""Training pace indicators: week-over-week change, vs monthly average, and RYG status.

Pure functions over already-loaded ``strength_events`` / ``cardio_events`` rows.
Indicators follow sports-science load-monitoring practice:

- **Acute:chronic ratio (ACWR):** calendar-week load ÷ average of prior four
  complete calendar weeks (monthly chronic baseline). Sweet spot ≈ 0.8–1.3;
  elevated injury-risk signal above ~1.5; underload below ~0.8.
- **Week-over-week % change:** flags rapid ramps (>10–15% cardio, >12–20%
  strength) and sharp drop-offs.
- **Vs monthly average %:** same chronic baseline expressed as percent delta.

Status lights use the **last completed** Mon–Sun week while the current week is
still in progress (partial weeks otherwise look falsely underloaded). Red is
reserved for **overload** spikes; underload caps at yellow with distinct labels.
Strength-typed Apple Health workouts are excluded from cardio minutes so they
are not double-counted against Hevy lifting volume.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pipeline.cardio_quality import (
    DEFAULT_RUN_PACE_MIN_SEC_MI,
    cardio_mode,
    is_overrecorded_distance,
    is_strength_like_cardio_activity,
)
from pipeline.features import as_date
from pipeline.mileage_ramp import iso_week_start
from pipeline.pace_thresholds import DEFAULT_PACE_THRESHOLDS
from pipeline.strength_analytics import calendar_week_volume_lbs

PaceStatus = Literal["green", "yellow", "red", "unknown"]
PaceDirection = Literal["high", "low"]

_STATUS_EMOJI: dict[PaceStatus, str] = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "unknown": "⚪",
}

_RANK: dict[PaceStatus, int] = {"unknown": 0, "green": 1, "yellow": 2, "red": 3}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _label_for(status: PaceStatus, direction: PaceDirection | None) -> str:
    if status == "green":
        return "Good to go"
    if status == "unknown":
        return "Building baseline"
    if direction == "low":
        return "Underloaded — room to push"
    if status == "red":
        return "Overloaded — take it easy"
    return "Cautious — ease up a little"


def _status_from_acwr(
    acwr: float | None, th: Mapping[str, float]
) -> tuple[PaceStatus, PaceDirection | None]:
    """Map ACWR to status. Underload caps at yellow; red is overload-only."""
    if acwr is None:
        return "unknown", None
    if acwr > th["pace_acwr_yellow_high"]:
        return "red", "high"
    if acwr > th["pace_acwr_green_high"]:
        return "yellow", "high"
    if acwr < th["pace_acwr_green_low"]:
        return "yellow", "low"
    return "green", None


def _status_from_wow(
    wow_pct: float | None,
    *,
    th: Mapping[str, float],
    spike_yellow: float,
    spike_red: float,
) -> tuple[PaceStatus, PaceDirection | None]:
    """WoW spikes can go red; drops cap at yellow (deload ≠ overload)."""
    if wow_pct is None:
        return "unknown", None
    if wow_pct >= spike_red:
        return "red", "high"
    if wow_pct >= spike_yellow:
        return "yellow", "high"
    if wow_pct <= -th["pace_wow_drop_yellow_pct"]:
        return "yellow", "low"
    return "green", None


def _status_from_vs_month(
    vs_pct: float | None, th: Mapping[str, float]
) -> tuple[PaceStatus, PaceDirection | None]:
    """Above-month spikes can go red; below-month caps at yellow."""
    if vs_pct is None:
        return "unknown", None
    if vs_pct >= th["pace_vs_month_red_pct"]:
        return "red", "high"
    if vs_pct >= th["pace_vs_month_yellow_pct"]:
        return "yellow", "high"
    if vs_pct <= -th["pace_vs_month_yellow_pct"]:
        return "yellow", "low"
    return "green", None


def _chronic_four_week_avg(loads: Sequence[float], idx: int) -> float | None:
    """Average of the four calendar weeks immediately before index ``idx``."""
    if idx < 4:
        return None
    prior = loads[idx - 4 : idx]
    if not prior or all(v <= 0 for v in prior):
        return None
    return sum(prior) / len(prior)


def _enrich_weekly_rollups(
    loads: Sequence[float],
    week_starts: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, (week_start, load) in enumerate(zip(week_starts, loads, strict=True)):
        row: dict[str, Any] = {
            "week_start": week_start,
            "load": round(load, 2),
        }
        if idx == 0:
            row["wow_change_pct"] = None
            row["four_week_avg_load"] = None
            row["vs_monthly_avg_pct"] = None
            row["acwr"] = None
        else:
            prev = loads[idx - 1]
            if prev and prev > 0:
                row["wow_change_pct"] = round((load - prev) / prev * 100.0, 1)
            else:
                row["wow_change_pct"] = None
            chronic = _chronic_four_week_avg(loads, idx)
            row["four_week_avg_load"] = round(chronic, 2) if chronic is not None else None
            if chronic and chronic > 0:
                row["acwr"] = round(load / chronic, 3)
                row["vs_monthly_avg_pct"] = round((load - chronic) / chronic * 100.0, 1)
            else:
                row["acwr"] = None
                row["vs_monthly_avg_pct"] = None
        rows.append(row)
    return rows


def _compose_domain_status(
    *,
    acwr: float | None,
    wow_pct: float | None,
    vs_month_pct: float | None,
    th: Mapping[str, float],
    spike_yellow: float,
    spike_red: float,
) -> tuple[PaceStatus, PaceDirection | None, list[str]]:
    status: PaceStatus = "unknown"
    direction: PaceDirection | None = None
    signals: list[str] = []
    for label, st, direc in (
        ("acwr", *_status_from_acwr(acwr, th)),
        (
            "wow_change",
            *_status_from_wow(
                wow_pct, th=th, spike_yellow=spike_yellow, spike_red=spike_red
            ),
        ),
        ("vs_monthly_avg", *_status_from_vs_month(vs_month_pct, th)),
    ):
        if st != "unknown":
            signals.append(label)
        if _RANK[st] > _RANK[status]:
            status = st
            direction = direc
        elif st == status and direction is None and direc is not None:
            direction = direc
        elif st == status and direc == "high":
            # Prefer overload direction when severity ties (safer coaching copy).
            direction = "high"
    if status == "unknown" and (acwr is not None or wow_pct is not None):
        status = "green"
        direction = None
    return status, direction, signals


def _status_row_index(weekly_rollups: Sequence[Mapping[str, Any]], *, as_of: date) -> int:
    """Index of the week used for RYG status (last completed while in-progress)."""
    if not weekly_rollups:
        return -1
    latest = weekly_rollups[-1]
    week_start = as_date(latest.get("week_start"))
    if week_start is None:
        return -1
    week_end = week_start + timedelta(days=6)
    if as_of < week_end and len(weekly_rollups) >= 2:
        return -2
    return -1


def _domain_block(
    *,
    load_unit: str,
    weekly_rollups: Sequence[Mapping[str, Any]],
    as_of: date,
    th: Mapping[str, float],
    spike_yellow: float,
    spike_red: float,
) -> dict[str, Any]:
    latest = weekly_rollups[-1] if weekly_rollups else {}
    status_idx = _status_row_index(weekly_rollups, as_of=as_of)
    status_row: Mapping[str, Any] = (
        weekly_rollups[status_idx] if weekly_rollups else {}
    )
    status, direction, signals = _compose_domain_status(
        acwr=_num(status_row.get("acwr")),
        wow_pct=_num(status_row.get("wow_change_pct")),
        vs_month_pct=_num(status_row.get("vs_monthly_avg_pct")),
        th=th,
        spike_yellow=spike_yellow,
        spike_red=spike_red,
    )
    prior = weekly_rollups[-2] if len(weekly_rollups) >= 2 else {}
    return {
        "status": status,
        "direction": direction,
        "emoji": _STATUS_EMOJI[status],
        "label": _label_for(status, direction),
        "load_unit": load_unit,
        "this_week_load": latest.get("load"),
        "last_week_load": prior.get("load") if prior else None,
        "status_week_start": status_row.get("week_start"),
        "wow_change_pct": status_row.get("wow_change_pct"),
        "four_week_avg_load": status_row.get("four_week_avg_load"),
        "vs_monthly_avg_pct": status_row.get("vs_monthly_avg_pct"),
        "acwr": status_row.get("acwr"),
        "weekly_rollups": list(weekly_rollups),
        "contributing_signals": signals,
    }


def calendar_week_strength_load_lbs(
    strength_events: Sequence[Mapping[str, Any]],
    *,
    week_start: date,
) -> float:
    """Working-set volume (lb) for a Mon–Sun calendar week."""
    return calendar_week_volume_lbs(strength_events, week_start=week_start)


def _cardio_row_load(
    row: Mapping[str, Any],
    *,
    mode: str | None,
    metric: str,
    run_pace_min_sec_mi: float,
) -> float:
    if is_strength_like_cardio_activity(row.get("activity_type")):
        return 0.0
    if mode is not None and cardio_mode(row.get("activity_type")) != mode:
        return 0.0
    if metric == "minutes":
        return _num(row.get("duration_min")) or 0.0
    if metric == "miles":
        if cardio_mode(row.get("activity_type")) == "running" and is_overrecorded_distance(
            row, run_pace_min_sec_mi=run_pace_min_sec_mi
        ):
            return 0.0
        return _num(row.get("distance_miles")) or 0.0
    return 0.0


def calendar_week_cardio_load(
    cardio_events: Sequence[Mapping[str, Any]],
    *,
    week_start: date,
    mode: str | None = None,
    metric: str = "minutes",
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
) -> float:
    """Sum cardio load for ``[week_start, week_start+6]`` (mode=None = all cardio).

    Strength-typed Apple Health workouts (Traditional Strength Training, etc.)
    are excluded so they are not double-counted against Hevy lifting volume.
    """
    week = {week_start + timedelta(days=i) for i in range(7)}
    total = 0.0
    for row in cardio_events:
        d = as_date(row.get("event_date"))
        if d not in week:
            continue
        total += _cardio_row_load(
            row, mode=mode, metric=metric, run_pace_min_sec_mi=run_pace_min_sec_mi
        )
    return round(total, 2)


def calendar_week_cardio_sessions(
    cardio_events: Sequence[Mapping[str, Any]],
    *,
    week_start: date,
) -> int:
    """Distinct calendar days with non-strength cardio in the Mon–Sun week."""
    week = {week_start + timedelta(days=i) for i in range(7)}
    days: set[date] = set()
    for row in cardio_events:
        d = as_date(row.get("event_date"))
        if d not in week or is_strength_like_cardio_activity(row.get("activity_type")):
            continue
        if (_num(row.get("duration_min")) or 0.0) > 0:
            days.add(d)
    return len(days)


def weekly_load_rollups(
    events: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    weeks: int,
    week_load_fn: Any,
) -> list[dict[str, Any]]:
    """Generic calendar-week load series with WoW / ACWR enrichment."""
    if weeks < 1:
        return []
    anchor = iso_week_start(as_of)
    loads: list[float] = []
    week_starts: list[str] = []
    for offset in range(weeks - 1, -1, -1):
        week_start = anchor - timedelta(days=7 * offset)
        load = float(week_load_fn(events, week_start=week_start))
        loads.append(load)
        week_starts.append(week_start.isoformat())
    return _enrich_weekly_rollups(loads, week_starts)


def build_workload_pace_summary(
    *,
    strength_events: Sequence[Mapping[str, Any]],
    cardio_events: Sequence[Mapping[str, Any]],
    as_of: date,
    lookback_weeks: int = 8,
    thresholds: Mapping[str, float] | None = None,
    run_pace_min_sec_mi: float = DEFAULT_RUN_PACE_MIN_SEC_MI,
) -> dict[str, Any]:
    """Compact workload-pace block for dashboard, briefing, and rules."""
    th = {**DEFAULT_PACE_THRESHOLDS, **(thresholds or {})}

    lifting_weekly = weekly_load_rollups(
        strength_events,
        as_of=as_of,
        weeks=lookback_weeks,
        week_load_fn=lambda ev, week_start: calendar_week_strength_load_lbs(
            ev, week_start=week_start
        ),
    )
    lifting = _domain_block(
        load_unit="lb",
        weekly_rollups=lifting_weekly,
        as_of=as_of,
        th=th,
        spike_yellow=th["pace_wow_spike_yellow_strength_pct"],
        spike_red=th["pace_wow_spike_red_strength_pct"],
    )

    cardio_minutes_weekly = weekly_load_rollups(
        cardio_events,
        as_of=as_of,
        weeks=lookback_weeks,
        week_load_fn=lambda ev, week_start: calendar_week_cardio_load(
            ev, week_start=week_start, mode=None, metric="minutes"
        ),
    )
    cardio = _domain_block(
        load_unit="min",
        weekly_rollups=cardio_minutes_weekly,
        as_of=as_of,
        th=th,
        spike_yellow=th["pace_wow_spike_yellow_cardio_pct"],
        spike_red=th["pace_wow_spike_red_cardio_pct"],
    )

    running_miles_weekly = weekly_load_rollups(
        cardio_events,
        as_of=as_of,
        weeks=lookback_weeks,
        week_load_fn=lambda ev, week_start: calendar_week_cardio_load(
            ev,
            week_start=week_start,
            mode="running",
            metric="miles",
            run_pace_min_sec_mi=run_pace_min_sec_mi,
        ),
    )
    cycling_miles_weekly = weekly_load_rollups(
        cardio_events,
        as_of=as_of,
        weeks=lookback_weeks,
        week_load_fn=lambda ev, week_start: calendar_week_cardio_load(
            ev,
            week_start=week_start,
            mode="cycling",
            metric="miles",
        ),
    )

    return {
        "as_of": as_of.isoformat(),
        "lifting": lifting,
        "cardio": cardio,
        "running": {
            "load_unit": "mi",
            "weekly_rollups": running_miles_weekly,
        },
        "cycling": {
            "load_unit": "mi",
            "weekly_rollups": cycling_miles_weekly,
        },
    }


def pace_status_message(domain: Mapping[str, Any]) -> str:
    """One-line human summary for email / dashboard chips."""
    status = str(domain.get("status") or "unknown")
    label = str(domain.get("label") or _label_for(status, domain.get("direction")))  # type: ignore[arg-type]
    emoji = str(domain.get("emoji") or _STATUS_EMOJI.get(status, "⚪"))  # type: ignore[arg-type]
    bits: list[str] = [f"{emoji} {label}"]
    acwr = domain.get("acwr")
    wow = domain.get("wow_change_pct")
    if isinstance(acwr, (int, float)):
        bits.append(f"ACWR {acwr:.2f}")
    if isinstance(wow, (int, float)):
        bits.append(f"WoW {wow:+.1f}%")
    vs = domain.get("vs_monthly_avg_pct")
    if isinstance(vs, (int, float)):
        bits.append(f"vs 4-wk avg {vs:+.1f}%")
    return " · ".join(bits)

"""Phase 9 Streamlit dashboard (Slices A–C + Phase 10 guidelines).

Fixture mode: ``SOMA_DASHBOARD_FIXTURE=1`` (or omit DB env vars).
Live mode: ``SOMA_USER_ID`` + ``SOMA_DATABASE_URL`` (or ``DB_CONNECT_STRING``).
Auth mode: ``SUPABASE_URL`` + ``SUPABASE_ANON_KEY`` for sign-in UI.

Run: ``streamlit run dashboard/app.py`` (requires ``pip install -e '.[dashboard]'``).
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pipeline.coaching_chat import load_chat_messages, run_coaching_turn, save_chat_messages
from pipeline.dashboard_queries import (
    fetch_cardio_events_window,
    fetch_features_history,
    fetch_metrics_history,
    fetch_strength_events_window,
    fetch_weekly_summaries,
    fetch_workout_calendar,
    load_dashboard_context_from_db,
    summarize_cardio_by_mode,
)
from pipeline.strength_analytics import build_strength_progress_summary
from pipeline.workout_calendar import build_workout_calendar
from pipeline.workload_pace import build_workload_pace_summary, pace_status_message
from pipeline.db_session import apply_rls_scope
from pipeline.goal_tools import apply_coaching_writes
from pipeline.guidelines import (
    GuidelinesContext,
    append_goal_note,
    load_guidelines,
    load_guidelines_from_env,
    resolve_guidelines_storage,
)
from pipeline.history_query import QueryAll
from pipeline.units import km_to_miles

try:
    import streamlit as st
except ImportError as exc:
    raise SystemExit(
        "Streamlit not installed. Run: pip install -e '.[dashboard]'"
    ) from exc

_REPO_ROOT = Path(__file__).resolve().parents[1]

# `streamlit run dashboard/app.py` puts dashboard/ (not the repo root) on sys.path,
# so `import dashboard.*` (e.g. dashboard.auth) needs the repo root added explicitly.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_REPO_ROOT / ".env")


def _apply_streamlit_secrets() -> None:
    """Map Streamlit Community Cloud secrets into ``os.environ`` when unset.

    ``st.secrets`` parses lazily, so membership tests raise when no
    ``secrets.toml`` exists (the common local / fixture case). Force the parse
    up front and bail out quietly so offline runs never crash.
    """
    try:
        secrets = st.secrets
        available = set(secrets.keys())
    except Exception:
        return
    for key in (
        "ENV",
        "SOMA_DASHBOARD_FIXTURE",
        "SOMA_CLOUD_DASHBOARD",
        "SOMA_DATABASE_URL",
        "DB_CONNECT_STRING",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "ANTHROPIC_API_KEY",
        "SOMA_GUIDELINES_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
    ):
        if key in available and not os.environ.get(key, "").strip():
            os.environ[key] = str(secrets[key])


def _resolve_db_url() -> str:
    for key in ("SOMA_DATABASE_URL", "DB_CONNECT_STRING", "DATABASE_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _fixture_mode_enabled() -> bool:
    explicit = os.environ.get("SOMA_DASHBOARD_FIXTURE", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    return not (_resolve_db_url() and _session_user_id())


def _session_user_id() -> str:
    if st.session_state.get("auth_user_id"):
        return str(st.session_state.auth_user_id)
    return os.environ.get("SOMA_USER_ID", "").strip()


@contextmanager
def _pg_conn() -> Iterator[object]:
    import psycopg2

    db_url = _resolve_db_url()
    if not db_url:
        raise RuntimeError("SOMA_DATABASE_URL not set")
    conn = psycopg2.connect(db_url)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _scoped_conn(user_id: str, *, read_only: bool = True) -> Iterator[object]:
    """A live DB connection bound to ``user_id`` under RLS as its first statement."""
    with _pg_conn() as conn:
        apply_rls_scope(conn, user_id=user_id, read_only=read_only)
        yield conn


def _resolve_llm(ctx: dict):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        from pipeline.briefing import DEFAULT_BRIEFING_MODEL
        from pipeline.clients import anthropic_llm

        return anthropic_llm(
            api_key,
            model=os.environ.get("BRIEFING_MODEL", DEFAULT_BRIEFING_MODEL),
        )

    def _mock_llm(system: str, prompt: str) -> str:
        # Dispatch on the system prompt so the tool-schema text (which mentions
        # "SQL") in a chat prompt can't be mistaken for a SQL-generation request.
        if "PostgreSQL SELECT" in system:  # generate_bounded_sql
            uid = ctx.get("user_id", "demo-user")
            return (
                f"SELECT metric_date, AVG(sleep_hours) AS avg_sleep "
                f"FROM daily_health_metrics "
                f"WHERE user_id = '{uid}' "
                f"GROUP BY metric_date ORDER BY metric_date DESC LIMIT 30"
            )
        if "query results" in system:  # summarize_query_result
            return "Demo summary: sleep looks steady (set ANTHROPIC_API_KEY for real analysis)."
        # Coaching chat: route trend/history questions through the query_history tool
        # so the demo exercises the folded-in text-to-SQL path. Keywords are specific
        # phrases unlikely to appear in the embedded context JSON.
        if any(kw in prompt.lower() for kw in ("trend", "how has", "over the last", "past 30", "past 7")):
            return '{"tool_calls": [{"name": "query_history", "arguments": {"question": "sleep trend"}}]}'
        return (
            f"Based on your data: {ctx.get('todays_focus', 'stay consistent')}. "
            "Set ANTHROPIC_API_KEY in .env for real replies."
        )

    return _mock_llm


def _fixture_strength_events(as_of: date) -> list[dict[str, Any]]:
    """Synthetic Hevy-style rows for offline strength analytics charts."""
    events: list[dict[str, Any]] = []
    upper_specs = [
        ("Bench Press (Dumbbell)", [140, 145, 150, 152, 155, 157, 160, 160]),
        ("Bicep Curl (Dumbbell)", [30, 32, 32, 35, 35, 37.5, 37.5, 40]),
        ("Incline Press (Dumbbell)", [55, 57.5, 60, 60, 62.5, 62.5, 65, 65]),
    ]
    lower_specs = [
        ("Squat (Barbell)", [185, 195, 205, 205, 215, 215, 225, 225]),
        ("Romanian Deadlift (Barbell)", [135, 145, 155, 155, 165, 165, 175, 175]),
    ]
    for week_i in range(8):
        upper_day = as_of - timedelta(days=(7 - week_i) * 7 + 1)
        lower_day = as_of - timedelta(days=(7 - week_i) * 7 + 3)
        for name, progression in upper_specs:
            weight = progression[week_i]
            for reps in (10, 8):
                events.append(
                    {
                        "event_date": upper_day.isoformat(),
                        "exercise_name": name,
                        "set_type": "working",
                        "reps": reps,
                        "weight_lbs": weight,
                    }
                )
        for name, progression in lower_specs:
            weight = progression[week_i]
            for reps in (8, 6):
                events.append(
                    {
                        "event_date": lower_day.isoformat(),
                        "exercise_name": name,
                        "set_type": "working",
                        "reps": reps,
                        "weight_lbs": weight,
                    }
                )
    return events


def _fixture_training_phase(as_of: date) -> dict[str, Any]:
    start = as_of - timedelta(days=14)
    end = as_of + timedelta(days=28)
    total_days = (end - start).days + 1
    elapsed = (as_of - start).days + 1
    weeks_total = max(1, (total_days + 6) // 7)
    weeks_elapsed = min(weeks_total, (elapsed + 6) // 7)
    active = {
        "id": "demo-phase-active",
        "name": "6-week building block",
        "phase_type": "building",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "notes": "Progressive overload on main lifts.",
        "target_notes": "3–4 strength days; keep weekly volume under ~15% jumps.",
        "is_active": True,
        "weeks_total": weeks_total,
        "weeks_elapsed": weeks_elapsed,
        "weeks_remaining": max(0, weeks_total - weeks_elapsed),
        "pct_complete": round(elapsed / total_days * 100.0, 1),
        "days_remaining": max(0, (end - as_of).days),
    }
    upcoming = [
        {
            "id": "demo-phase-deload",
            "name": "Deload / recovery",
            "phase_type": "deload",
            "start_date": (end + timedelta(days=1)).isoformat(),
            "end_date": (end + timedelta(days=7)).isoformat(),
            "notes": "Reduce volume ~40% before the next block.",
            "target_notes": None,
            "is_active": True,
        }
    ]
    return {
        "as_of": as_of.isoformat(),
        "active": active,
        "upcoming": upcoming,
        "all_phases": [active, *upcoming],
    }


def _fixture_athlete_journal(as_of: date) -> list[dict[str, Any]]:
    return [
        {
            "id": "demo-journal-1",
            "entry_date": as_of.isoformat(),
            "category": "workout",
            "body": "Chest press felt challenging today — barely hit my working weights.",
            "logged_at": f"{as_of.isoformat()}T12:00:00",
        },
        {
            "id": "demo-journal-2",
            "entry_date": (as_of - timedelta(days=21)).isoformat(),
            "category": "supplement",
            "body": "Started creatine (~5g/day).",
            "logged_at": f"{(as_of - timedelta(days=21)).isoformat()}T08:00:00",
        },
    ]


def _fixture_context() -> dict:
    from pipeline.dashboard_queries import build_dashboard_context

    today = date.today()
    strength_events = _fixture_strength_events(today)
    cardio_events = _fixture_cardio_events(today)
    strength_progress = build_strength_progress_summary(strength_events, as_of=today)
    workload_pace = build_workload_pace_summary(
        strength_events=strength_events,
        cardio_events=cardio_events,
        as_of=today,
    )
    return build_dashboard_context(
        user_id="demo-user",
        as_of=today,
        latest_briefing={
            "briefing_date": today,
            "coaching_note": "Sleep was short — prioritize recovery before heavy lower body.",
            "flags": ["HIGH_SLEEP_DEBT"],
        },
        latest_features={
            "feature_date": today,
            "strength_sessions_7d": 1,
            "cardio_minutes_7d": 45,
            "training_load_cardio_minutes_7d": 45,
            "training_load_cardio_minutes_28d": 180,
            "training_load_strength_short_tons_7d": 2.1,
            "effort_unified_index_7d": 12.5,
            "overall_readiness_score": 62,
        },
        latest_metrics={
            "metric_date": today,
            "hrv_rmssd": 48,
            "sleep_hours": 5.8,
            "resting_hr": 58,
        },
        goal_snapshot={
            "goals_status": {
                "strength": {"completed": 1, "target": "3-4x", "status": "behind"},
            },
            "todays_focus": "Strength session needed — 1 of 3-4x done",
            "mileage_check": {"flag": None, "this_week_km": 6.4, "last_week_km": 9.1},
        },
        weekly_summary={
            "week_start": today,
            "strength_sessions": 1,
            "running_km": 6.4,
            "cardio_minutes": 45,
            "summary_json": {
                "strength_volume_lbs": strength_progress.get("this_week_volume_lbs"),
                "strength_volume_wow_change_pct": strength_progress.get("week_over_week_change_pct"),
            },
        },
        strength_progress=strength_progress,
        workload_pace=workload_pace,
        training_phase=_fixture_training_phase(today),
        athlete_journal=_fixture_athlete_journal(today),
    )


def _fixture_guidelines() -> GuidelinesContext:
    return GuidelinesContext(
        my_goals="Build to 3-4 strength sessions per week. Keep weekly running mileage progressive.",
        injury_history="Left shoulder impingement — avoid heavy overhead pressing.",
    )


@st.cache_data(ttl=60)
def _load_live_context(user_id: str, as_of_iso: str) -> dict:
    with _scoped_conn(user_id) as conn:
        return load_dashboard_context_from_db(
            conn,
            user_id=user_id,
            as_of=date.fromisoformat(as_of_iso),
        )


# ---------------------------------------------------------------------------
# Trend history (powers the charts) — live DB reads or synthesized fixtures.
# ---------------------------------------------------------------------------


def _fixture_metrics_history(as_of: date, days: int) -> list[dict[str, Any]]:
    """Deterministic, realistic daily biometrics so charts render fully offline.

    Values are seeded by calendar date (stable across reruns) and gently
    correlated (weekly sleep/HRV waves, a slow weight downtrend). The final day
    is pinned to the ``_fixture_context`` snapshot so the headline tiles agree
    with the end of each trend line.
    """
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        day = as_of - timedelta(days=days - 1 - offset)
        rng = random.Random(day.toordinal())
        wave = offset / 7.0
        sleep = round(6.9 + 0.9 * math.sin(wave) + rng.uniform(-0.6, 0.6), 1)
        rows.append(
            {
                "metric_date": day.isoformat(),
                "hrv_rmssd": round(50 + 8 * math.sin(wave / 1.3) + rng.uniform(-4, 4)),
                "resting_hr": round(56 - 3 * math.sin(wave / 1.3) + rng.uniform(-2, 2)),
                "spo2_pct": round(96 + rng.uniform(-1, 1.5), 1),
                "sleep_hours": sleep,
                "sleep_deep_hrs": round(max(0.4, sleep * 0.18 + rng.uniform(-0.2, 0.2)), 2),
                "sleep_rem_hrs": round(max(0.6, sleep * 0.22 + rng.uniform(-0.2, 0.2)), 2),
                "sleep_score": round(max(45, min(96, 78 + 10 * math.sin(wave) + rng.uniform(-6, 6)))),
                "steps": int(max(1500, 8500 + 2500 * math.sin(wave) + rng.uniform(-1500, 1500))),
                "active_cal": int(max(120, 470 + 150 * math.sin(wave) + rng.uniform(-80, 80))),
                "body_weight_lbs": round(179.5 - offset * 0.03 + rng.uniform(-0.4, 0.4), 1),
                "body_fat_pct": round(18.6 - offset * 0.004 + rng.uniform(-0.2, 0.2), 1),
            }
        )
    _attach_trailing_avg(rows, "sleep_hours", "sleep_7d_avg")
    _attach_trailing_avg(rows, "hrv_rmssd", "hrv_7d_avg")
    if rows:  # pin the latest day to the snapshot in _fixture_context()
        rows[-1].update(hrv_rmssd=48, sleep_hours=5.8, resting_hr=58)
    return rows


def _fixture_features_history(as_of: date, days: int) -> list[dict[str, Any]]:
    """Deterministic daily features (readiness / ACWR / load / effort)."""
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        day = as_of - timedelta(days=days - 1 - offset)
        rng = random.Random(day.toordinal() + 1000)
        wave = offset / 7.0
        cardio = round(max(0, 120 + 55 * math.sin(wave) + rng.uniform(-20, 20)))
        tonnage = round(max(0.0, 2.0 + 0.8 * math.sin(wave / 1.5) + rng.uniform(-0.3, 0.3)), 1)
        rows.append(
            {
                "feature_date": day.isoformat(),
                "overall_readiness_score": round(
                    max(35, min(92, 65 + 12 * math.sin(wave / 1.4) + rng.uniform(-6, 6)))
                ),
                "acute_chronic_ratio": round(
                    max(0.6, min(1.6, 1.0 + 0.25 * math.sin(wave / 2.0) + rng.uniform(-0.07, 0.07))), 2
                ),
                "cardio_minutes_7d": cardio,
                "training_load_cardio_minutes_7d": cardio,
                "training_load_cardio_minutes_28d": round(cardio * 3.6),
                "strength_tonnage_7d": tonnage,
                "strength_sessions_7d": int(max(0, round(2 + math.sin(wave / 1.5)))),
                "effort_unified_index_7d": round(max(0.0, cardio * 0.08 + tonnage * 3 + rng.uniform(-1, 1)), 1),
                "sleep_debt_7d": round(max(0.0, 4 - 2 * math.sin(wave) + rng.uniform(-1, 1)), 1),
            }
        )
    if rows:
        rows[-1]["overall_readiness_score"] = 62
    return rows


def _fixture_weekly_summaries(as_of: date, weeks: int) -> list[dict[str, Any]]:
    monday = as_of - timedelta(days=as_of.weekday())
    rows: list[dict[str, Any]] = []
    for back in range(weeks):
        week_start = monday - timedelta(weeks=weeks - 1 - back)
        rng = random.Random(week_start.toordinal())
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "strength_sessions": rng.randint(1, 4),
                "running_km": round(rng.uniform(5, 18), 1),
                "cardio_minutes": round(rng.uniform(60, 220)),
                "strength_volume_lbs": round(rng.uniform(12000, 28000), 1),
                "upper_volume_lbs": round(rng.uniform(5000, 14000), 1),
                "lower_volume_lbs": round(rng.uniform(4000, 12000), 1),
            }
        )
    return rows


def _fixture_cardio_events(as_of: date) -> list[dict[str, Any]]:
    """A deterministic cardio set for Training charts + the workout calendar.

    Distances are in miles (canonical for ``cardio_events``). Includes a
    Fitbit/Health Sync walk so the calendar demo shows bridged activity.
    """
    return [
        {
            "event_date": (as_of - timedelta(days=1)).isoformat(),
            "activity_type": "Outdoor Run",
            "distance_miles": 3.1,
            "duration_min": 27.0,
            "source": "apple_health",
            "source_app": "Nike Run Club",
        },
        {
            "event_date": (as_of - timedelta(days=3)).isoformat(),
            "activity_type": "Outdoor Run",
            "distance_miles": 4.2,
            "duration_min": 38.0,
            "source": "apple_health",
            "source_app": "Nike Run Club",
        },
        {
            "event_date": (as_of - timedelta(days=2)).isoformat(),
            "activity_type": "Outdoor Cycling",
            "distance_miles": 12.4,
            "duration_min": 48.0,
            "source": "apple_health",
            "source_app": "Mason's Apple Watch",
        },
        {
            "event_date": (as_of - timedelta(days=5)).isoformat(),
            "activity_type": "Walking",
            "distance_miles": 2.4,
            "duration_min": 42.0,
            "source": "apple_health",
            "source_app": "Health Sync",
        },
        {
            "event_date": (as_of - timedelta(days=12)).isoformat(),
            "activity_type": "Walking",
            "distance_miles": 1.8,
            "duration_min": 35.0,
            "source": "apple_health",
            "source_app": "Fitbit",
        },
        {
            "event_date": (as_of - timedelta(days=18)).isoformat(),
            "activity_type": "Outdoor Run",
            "distance_miles": 5.0,
            "duration_min": 46.0,
            "source": "apple_health",
            "source_app": "Nike Run Club",
        },
    ]


def _attach_trailing_avg(rows: list[dict[str, Any]], source: str, target: str, window: int = 7) -> None:
    values = [r.get(source) for r in rows]
    for i, row in enumerate(rows):
        chunk = [v for v in values[max(0, i - window + 1) : i + 1] if isinstance(v, (int, float))]
        row[target] = round(sum(chunk) / len(chunk), 1) if chunk else None


@st.cache_data(ttl=60)
def _load_history_live(user_id: str, as_of_iso: str, days: int) -> dict[str, list[dict[str, Any]]]:
    as_of = date.fromisoformat(as_of_iso)
    weeks = max(6, days // 7 + 1)
    with _scoped_conn(user_id) as conn:
        return {
            "metrics": fetch_metrics_history(conn, user_id=user_id, as_of=as_of, days=days),
            "features": fetch_features_history(conn, user_id=user_id, as_of=as_of, days=days),
            "weekly": fetch_weekly_summaries(conn, user_id=user_id, as_of=as_of, weeks=weeks),
        }


def _with_running_miles(weekly_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a display-ready ``running_miles`` column to weekly rollup rows.

    ``weekly_activity_summary`` stores ``running_km`` (base metric unit); the
    dashboard charts distance in miles, so convert once here for both live and
    fixture rows.
    """
    for row in weekly_rows:
        row["running_miles"] = km_to_miles(row.get("running_km"))
    return weekly_rows


def _load_history(ctx: dict, mode: str, days: int) -> dict[str, list[dict[str, Any]]]:
    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    if mode == "fixture":
        return {
            "metrics": _fixture_metrics_history(as_of, days),
            "features": _fixture_features_history(as_of, days),
            "weekly": _with_running_miles(_fixture_weekly_summaries(as_of, max(6, days // 7 + 1))),
        }
    try:
        history = _load_history_live(ctx["user_id"], as_of.isoformat(), days)
        history["weekly"] = _with_running_miles(history.get("weekly", []))
        return history
    except Exception:
        return {"metrics": [], "features": [], "weekly": []}


@st.cache_data(ttl=300)
def _load_guidelines_cached(user_id: str, fixture: bool) -> GuidelinesContext | None:
    if fixture:
        return _fixture_guidelines()
    ctx = load_guidelines_from_env(user_id)
    if ctx is not None:
        return ctx
    storage = resolve_guidelines_storage()
    if storage is None:
        return None
    get_object, _ = storage
    loaded = load_guidelines(user_id, get_object=get_object)
    return loaded if loaded.has_content() else None


def _load_guidelines(user_id: str) -> GuidelinesContext | None:
    return _load_guidelines_cached(user_id, _fixture_mode_enabled())


def _persist_coaching_writes(user_id: str, pending_writes: list[dict]) -> list[str]:
    storage = resolve_guidelines_storage()
    append_note = None
    if storage is not None:
        get_object, put_object = storage

        def _append(text: str) -> str:
            return append_goal_note(
                user_id, text, get_object=get_object, put_object=put_object
            )

        append_note = _append

    with _scoped_conn(user_id, read_only=False) as conn:
        with conn.cursor() as cur:
            applied = apply_coaching_writes(cur, pending_writes, append_note=append_note)
        conn.commit()
        return applied


def _cloud_dashboard() -> bool:
    return os.environ.get("SOMA_CLOUD_DASHBOARD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


# Persist the Supabase refresh token in a browser cookie so a full page
# reload (which discards st.session_state) can silently re-establish the
# session instead of forcing another sign-in.
_REFRESH_COOKIE = "soma_refresh_token"
_REFRESH_COOKIE_DAYS = 30
# Time to let the cookie component's browser round-trip finish before a rerun
# tears it down (write side) / before the login gate renders (read side).
_COOKIE_WRITE_SETTLE_SECONDS = 0.6


def _cookie_manager():
    """Return a CookieManager for this script run, or None if unavailable.

    A fresh instance is built on every run *on purpose*: ``CookieManager``
    reads the browser cookies inside ``__init__`` (via its ``getAll`` component)
    and caches them on the instance. Persisting the instance in
    ``st.session_state`` would freeze that snapshot at the very first run — when
    the component has not yet mounted and reports no cookies — so ``.get()``
    would return ``None`` forever and the refresh-token cookie could never be
    read back after a page reload. Rebuilding each run lets the (stable-key)
    component re-report the real cookies once it has mounted.
    """
    try:
        import extra_streamlit_components as stx
    except ImportError:
        return None
    return stx.CookieManager(key="soma_cookies")


def _store_refresh_cookie(cookie_manager, refresh_token: str) -> None:
    if cookie_manager is None or not refresh_token:
        return
    secure = os.environ.get("SOMA_CLOUD_DASHBOARD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cookie_manager.set(
        _REFRESH_COOKIE,
        refresh_token,
        key="soma_set_refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(days=_REFRESH_COOKIE_DAYS),
        secure=secure or None,
        same_site="lax",
    )
    # ``set`` is a frontend component: the cookie is only written after the
    # browser receives the render delta and runs its JS. Callers rerun right
    # after sign-in, which would tear the component down before that round-trip
    # completes and silently drop the cookie — so give the browser a beat to
    # persist it. (Supabase also rotates the refresh token on every refresh, so
    # dropping this write breaks the *next* reload, not just the current one.)
    time.sleep(_COOKIE_WRITE_SETTLE_SECONDS)


def _clear_refresh_cookie(cookie_manager) -> None:
    if cookie_manager is None:
        return
    if cookie_manager.get(_REFRESH_COOKIE) is not None:
        cookie_manager.delete(_REFRESH_COOKIE, key="soma_del_refresh")
        # Same browser round-trip caveat as ``_store_refresh_cookie``: without a
        # beat before the caller reruns, the delete component is torn down before
        # the browser clears the cookie, so sign-out silently leaves the
        # refresh token in place and the next load logs the user right back in.
        time.sleep(_COOKIE_WRITE_SETTLE_SECONDS)


def _restore_session_from_cookie(cookie_manager) -> None:
    """Rehydrate auth state from the refresh-token cookie on a fresh page load."""
    from dashboard.auth import AuthError, auth_configured, refresh_session

    if cookie_manager is None or st.session_state.get("auth_user_id"):
        return
    if st.session_state.get("_signed_out"):
        return
    if not auth_configured() or _fixture_mode_enabled():
        return
    refresh_token = cookie_manager.get(_REFRESH_COOKIE)
    if not refresh_token:
        return
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_ANON_KEY"].strip()
    try:
        session = refresh_session(
            refresh_token=str(refresh_token), supabase_url=url, anon_key=key
        )
    except AuthError:
        _clear_refresh_cookie(cookie_manager)
        return
    st.session_state.auth_user_id = session["user_id"]
    st.session_state.auth_email = session["email"]
    st.session_state.auth_token = session["access_token"]
    _store_refresh_cookie(cookie_manager, session["refresh_token"])


def _await_cookie_sync(cookie_manager) -> None:
    """Give the cookie component one render pass to report browser cookies.

    On a fresh page load the ``CookieManager`` component has not mounted yet, so
    the first script run sees no cookies. Rather than flash the login form (and
    lose the persisted session), do a single short settle-and-rerun so the next
    run can rehydrate from the refresh-token cookie. Guarded by a per-session
    flag so genuine first-time visitors only pay for it once.
    """
    from dashboard.auth import auth_configured

    if cookie_manager is None or st.session_state.get("auth_user_id"):
        return
    if st.session_state.get("_signed_out"):
        return
    if not auth_configured() or _fixture_mode_enabled():
        return
    if st.session_state.get("_cookie_synced"):
        return
    st.session_state._cookie_synced = True
    time.sleep(_COOKIE_WRITE_SETTLE_SECONDS)
    st.rerun()


# ---------------------------------------------------------------------------
# Presentation helpers (frontend-only; no data/auth logic lives here)
# ---------------------------------------------------------------------------

_TRUTHY = ("1", "true", "yes", "on")


def _debug_enabled() -> bool:
    """Developer/debug mode: OFF by default.

    Enabled via the sidebar toggle or a ``?debug=1`` query parameter. Gates all
    raw JSON / tool-result dumps so they never appear in the default view.
    """
    try:
        if str(st.query_params.get("debug", "")).strip().lower() in _TRUTHY:
            return True
    except Exception:
        pass
    return bool(st.session_state.get("dev_mode", False))


def _fmt(value: Any, *, suffix: str = "", dash: str = "—") -> str:
    """Human-friendly scalar formatting with a graceful empty state."""
    if value is None or value == "":
        return dash
    if isinstance(value, float):
        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"{text}{suffix}"
    return f"{value}{suffix}"


def _readiness_meta(score: Any) -> tuple[str, str]:
    """Return (emoji, label) for a readiness score, or a neutral default."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ("", "")
    if value >= 75:
        return ("🟢", "High")
    if value >= 50:
        return ("🟡", "Moderate")
    return ("🔴", "Low")


def _pace_light_card(title: str, domain: Mapping[str, Any] | None) -> None:
    """Render a single workload-pace traffic light with supporting metrics."""
    if not domain:
        st.caption(f"{title}: no data yet")
        return
    emoji = str(domain.get("emoji") or "⚪")
    label = str(domain.get("label") or "Building baseline")
    st.markdown(f"### {emoji} {title}", help=pace_status_message(domain))
    st.caption(label)
    load = domain.get("acute_load")
    unit = str(domain.get("load_unit") or "")
    if load is not None:
        st.metric("Last 7 days", _fmt(load, suffix=f" {unit}"))
    wow = domain.get("wow_change_pct")
    if isinstance(wow, (int, float)):
        st.caption(f"Vs prior 7 days: {wow:+.1f}%")
    acwr = domain.get("acwr")
    if isinstance(acwr, (int, float)):
        st.caption(f"ACWR (vs 4×7d avg): {acwr:.2f}")
    vs = domain.get("vs_monthly_avg_pct")
    if isinstance(vs, (int, float)):
        st.caption(f"Vs 28d avg: {vs:+.1f}%")


def _render_workload_pace_lights(workload_pace: dict[str, Any] | None) -> None:
    """Hero/overview strip: lifting + cardio traffic lights."""
    if not workload_pace:
        return
    st.markdown("**Training pace**")
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            _pace_light_card("Lifting", workload_pace.get("lifting"))
    with right:
        with st.container(border=True):
            _pace_light_card("Cardio", workload_pace.get("cardio"))


_GOAL_STATUS_META: dict[str, tuple[str, str]] = {
    "done": ("✅", "Done"),
    "complete": ("✅", "Complete"),
    "on_track": ("✅", "On track"),
    "ahead": ("🔥", "Ahead"),
    "behind": ("⚠️", "Behind"),
    "at_risk": ("⚠️", "At risk"),
    "not_yet": ("⬜", "Not yet"),
    "missed": ("❌", "Missed"),
}


def _goal_status_meta(status: Any) -> tuple[str, str]:
    if not status:
        return ("•", "")
    key = str(status).strip().lower()
    return _GOAL_STATUS_META.get(key, ("•", str(status).replace("_", " ").title()))


def _is_leaf_goal(value: Any) -> bool:
    return isinstance(value, dict) and any(
        k in value for k in ("status", "completed", "target", "done")
    )


def _goal_detail(leaf: dict) -> str:
    """Compose a short right-hand detail string for a single goal row."""
    parts: list[str] = []
    completed = leaf.get("completed")
    target = leaf.get("target")
    if completed is not None and target is not None:
        parts.append(f"{completed} / {target}")
    elif completed is not None:
        parts.append(str(completed))
    elif target is not None:
        parts.append(f"target {target}")
    emoji, label = _goal_status_meta(leaf.get("status"))
    if label:
        parts.append(f"{emoji} {label}")
    elif leaf.get("done") is not None:
        parts.append("✅ Done" if leaf.get("done") else "⬜ Not yet")
    return " · ".join(parts) if parts else "—"


_SEVERITY_EMOJI = {
    "high": "🔴",
    "critical": "🔴",
    "medium": "🟡",
    "warning": "🟡",
    "low": "🔵",
    "info": "🔵",
}


# ---------------------------------------------------------------------------
# Chart helpers (pandas ships with Streamlit; used only for visualization)
# ---------------------------------------------------------------------------


def _history_df(rows: list[dict[str, Any]], date_key: str):
    """Rows -> a numeric, date-indexed DataFrame ready for st.*_chart."""
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if date_key not in df.columns:
        return pd.DataFrame()
    df[date_key] = pd.to_datetime(df[date_key], errors="coerce")
    df = df.dropna(subset=[date_key]).sort_values(date_key).set_index(date_key)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _enrich_metrics_trailing_avgs(mdf):
    """Fill ``*_7d_avg`` from the daily series when the pipeline left them null.

    Live ``daily_health_metrics`` often has sleep/HRV values but never computes
    the trailing averages (fixtures do). Charts that overlay "7-day avg" need
    these columns populated client-side.
    """
    if mdf is None or getattr(mdf, "empty", True):
        return mdf
    pairs = (("sleep_hours", "sleep_7d_avg"), ("hrv_rmssd", "hrv_7d_avg"))
    for source, target in pairs:
        if source not in mdf.columns or mdf[source].dropna().empty:
            continue
        need = target not in mdf.columns or mdf[target].dropna().empty
        if need:
            mdf[target] = mdf[source].rolling(window=7, min_periods=1).mean()
    return mdf


def _enrich_metrics_sleep_scores(mdf):
    """Fill missing ``sleep_score`` from sleep duration / stages on the chart frame.

    Historical nights often have ``sleep_hours`` but a null score because the
    daily rollup only writes a score for ``run_date``. Compute the native Soma
    score client-side so the Sleep tab graph is not empty.
    """
    if mdf is None or getattr(mdf, "empty", True):
        return mdf
    if "sleep_hours" not in mdf.columns or mdf["sleep_hours"].dropna().empty:
        return mdf
    if "sleep_score" in mdf.columns and mdf["sleep_score"].notna().all():
        return mdf

    from pipeline.sleep_score import fill_missing_sleep_scores

    date_col = mdf.index.name or "metric_date"
    records = mdf.reset_index().to_dict(orient="records")
    for row in records:
        if date_col != "metric_date" and date_col in row:
            row["metric_date"] = row[date_col]
    by_date = {
        str(r["metric_date"])[:10]: r.get("sleep_score")
        for r in fill_missing_sleep_scores(records)
        if r.get("metric_date") is not None
    }

    def _key(idx: Any) -> str:
        if hasattr(idx, "date") and callable(idx.date) and not isinstance(idx, date):
            idx = idx.date()
        return str(idx)[:10]

    mdf = mdf.copy()
    mdf["sleep_score"] = [by_date.get(_key(idx)) for idx in mdf.index]
    return mdf


def _enrich_metrics_frame(mdf):
    """Apply all client-side metric enrichments for dashboard charts."""
    return _enrich_metrics_trailing_avgs(_enrich_metrics_sleep_scores(mdf))


def _series_has_data(df, col: str) -> bool:
    return (
        df is not None
        and not getattr(df, "empty", True)
        and col in df.columns
        and bool(df[col].notna().any())
    )


# Chart x-axis grain: every dashboard time series is either daily or calendar-week.
Grain = Literal["day", "week"]
_GRAIN_X_TITLE: dict[str, str] = {
    "day": "Day",
    "week": "Week starting (Mon)",
}


def _sleep_stages_chart(mdf, *, empty: str = "No deep/REM stage hours in this range yet.") -> None:
    """Stacked deep / REM / light hours for nights that have stage data.

    Filters out blank nights so a sparse series does not render as a single
    orphaned bar on an empty calendar axis. Light = total sleep − deep − REM
    when total sleep is known (clamped at 0).
    """
    if mdf is None or getattr(mdf, "empty", True):
        st.caption(empty)
        return
    need = [c for c in ("sleep_deep_hrs", "sleep_rem_hrs") if c in mdf.columns]
    if not need:
        st.caption(empty)
        return
    stage_nights = mdf.dropna(subset=need, how="all")
    if stage_nights.empty:
        st.caption(empty)
        return

    import altair as alt
    import pandas as pd

    plot = stage_nights.reset_index()
    date_col = plot.columns[0]
    deep = plot["sleep_deep_hrs"] if "sleep_deep_hrs" in plot.columns else 0.0
    rem = plot["sleep_rem_hrs"] if "sleep_rem_hrs" in plot.columns else 0.0
    deep = pd.to_numeric(deep, errors="coerce").fillna(0.0)
    rem = pd.to_numeric(rem, errors="coerce").fillna(0.0)
    if "sleep_hours" in plot.columns:
        total = pd.to_numeric(plot["sleep_hours"], errors="coerce")
        light = (total - deep - rem).clip(lower=0.0).fillna(0.0)
    else:
        light = pd.Series(0.0, index=plot.index)

    long = pd.DataFrame(
        {
            date_col: list(plot[date_col]) * 3,
            "stage": (["Deep"] * len(plot)) + (["REM"] * len(plot)) + (["Light / other"] * len(plot)),
            "hours": list(deep) + list(rem) + list(light),
        }
    )
    # Drop zero-height segments so the legend stays honest.
    long = long[long["hours"] > 0.01]
    if long.empty:
        st.caption(empty)
        return

    x_title = _GRAIN_X_TITLE["day"]
    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X(
                f"{date_col}:T",
                title=x_title,
                axis=alt.Axis(format="%b %d", labelAngle=-30),
            ),
            y=alt.Y("hours:Q", title="Hours", stack="zero"),
            color=alt.Color(
                "stage:N",
                title=None,
                scale=alt.Scale(
                    domain=["Deep", "REM", "Light / other"],
                    range=["#3b5bdb", "#ae3ec9", "#adb5bd"],
                ),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip(f"{date_col}:T", title=x_title, format="%Y-%m-%d"),
                "stage:N",
                alt.Tooltip("hours:Q", format=".1f"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)

    n = len(stage_nights)
    deep_pct = (deep.sum() / max(deep.sum() + rem.sum() + light.sum(), 1e-6)) * 100.0
    rem_pct = (rem.sum() / max(deep.sum() + rem.sum() + light.sum(), 1e-6)) * 100.0
    st.caption(
        f"{n} night{'s' if n != 1 else ''} with stage data · "
        f"avg mix Deep {deep_pct:.0f}% / REM {rem_pct:.0f}% "
        f"(targets ~18% deep, ~22% REM of total sleep)."
    )


def _time_chart(
    df,
    series: dict[str, str],
    *,
    grain: Grain,
    kind: str = "line",
    empty: str = "No data in this range yet.",
    y_title: str | None = None,
    height: int = 260,
) -> None:
    """Render a time series with an explicit Day / Week-starting x-axis.

    ``grain=\"day\"`` → daily points (biometrics, readiness, sleep debt).
    ``grain=\"week\"`` → ISO calendar weeks keyed by Monday week-start.
    """
    if df is None or getattr(df, "empty", True):
        st.caption(empty)
        return
    present = {col: label for col, label in series.items() if col in df.columns and df[col].notna().any()}
    if not present:
        st.caption(empty)
        return

    import altair as alt
    import pandas as pd

    data = df[list(present)].rename(columns=present).reset_index()
    date_col = data.columns[0]
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    data = data.dropna(subset=[date_col])
    long_df = data.melt(id_vars=[date_col], var_name="series", value_name="value").dropna(
        subset=["value"]
    )
    if long_df.empty:
        st.caption(empty)
        return

    x_title = _GRAIN_X_TITLE[grain]
    x_enc = alt.X(
        f"{date_col}:T",
        title=x_title,
        axis=alt.Axis(format="%b %d", labelAngle=-30),
    )
    y_enc = alt.Y("value:Q", title=y_title or "")
    color = alt.Color("series:N", title=None, legend=alt.Legend(orient="top"))
    tooltips = [
        alt.Tooltip(f"{date_col}:T", title=x_title, format="%Y-%m-%d"),
        "series:N",
        alt.Tooltip("value:Q", format=".1f"),
    ]

    if kind == "bar":
        mark = alt.Chart(long_df).mark_bar()
    elif kind == "area":
        mark = alt.Chart(long_df).mark_area(opacity=0.65)
    else:
        mark = alt.Chart(long_df).mark_line(point=grain == "week")

    chart = mark.encode(x=x_enc, y=y_enc, color=color, tooltip=tooltips).properties(height=height)
    st.altair_chart(chart, use_container_width=True)


def _chart(
    df,
    series: dict[str, str],
    *,
    grain: Grain = "day",
    kind: str = "line",
    empty: str = "No data in this range yet.",
    y_title: str | None = None,
) -> None:
    """Backward-compatible wrapper — prefer passing ``grain`` explicitly."""
    _time_chart(df, series, grain=grain, kind=kind, empty=empty, y_title=y_title)


def _padded_chart(
    df,
    series: dict[str, str],
    *,
    pad: float,
    grain: Grain = "day",
    empty: str = "No data in this range yet.",
    y_title: str | None = None,
) -> None:
    """Line chart with a non-zero, padded y-domain so variation is visible.

    Streamlit's native ``st.line_chart`` always anchors the y-axis at 0, which
    flattens tight biometric ranges (weight, resting HR, HRV…). Uses the same
    Day / Week-starting x-axis convention as :func:`_time_chart`.
    """
    if df is None or getattr(df, "empty", True):
        st.caption(empty)
        return
    present = {c: l for c, l in series.items() if c in df.columns and df[c].notna().any()}
    if not present:
        st.caption(empty)
        return
    import altair as alt
    import pandas as pd

    data = df[list(present)].rename(columns=present).reset_index()
    date_col = data.columns[0]
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    long_df = data.melt(id_vars=[date_col], var_name="series", value_name="value").dropna(
        subset=["value"]
    )
    if long_df.empty:
        st.caption(empty)
        return
    lo = float(long_df["value"].min()) - pad
    hi = float(long_df["value"].max()) + pad
    if lo == hi:
        lo, hi = lo - pad, hi + pad
    x_title = _GRAIN_X_TITLE[grain]
    chart = (
        alt.Chart(long_df)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                f"{date_col}:T",
                title=x_title,
                axis=alt.Axis(format="%b %d", labelAngle=-30),
            ),
            y=alt.Y(
                "value:Q",
                scale=alt.Scale(domain=[lo, hi], zero=False),
                title=y_title or "",
            ),
            color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{date_col}:T", title=x_title, format="%Y-%m-%d"),
                "series:N",
                alt.Tooltip("value:Q", format=".1f"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(chart, use_container_width=True)


def _acwr_band_chart(df, *, empty: str = "No ACWR history yet.") -> None:
    """Calendar-week ACWR with the 0.8–1.3 sweet-spot band shaded."""
    if df is None or getattr(df, "empty", True) or "acwr" not in df.columns:
        st.caption(empty)
        return
    series = df["acwr"].dropna()
    if series.empty:
        st.caption(empty)
        return
    import altair as alt
    import pandas as pd

    plot = series.reset_index()
    date_col = plot.columns[0]
    plot[date_col] = pd.to_datetime(plot[date_col], errors="coerce")
    plot = plot.rename(columns={"acwr": "ACWR"}).dropna(subset=[date_col])
    lo = max(0.0, float(plot["ACWR"].min()) - 0.2)
    hi = max(1.8, float(plot["ACWR"].max()) + 0.2)
    band = pd.DataFrame(
        {date_col: [plot[date_col].min(), plot[date_col].max()], "y": [0.8, 0.8], "y2": [1.3, 1.3]}
    )
    x_title = _GRAIN_X_TITLE["week"]
    x_enc = alt.X(
        f"{date_col}:T",
        title=x_title,
        axis=alt.Axis(format="%b %d", labelAngle=-30),
    )
    band_layer = (
        alt.Chart(band)
        .mark_area(opacity=0.18, color="#2ecc71")
        .encode(x=x_enc, y="y:Q", y2="y2:Q")
    )
    line = (
        alt.Chart(plot)
        .mark_line(point=True)
        .encode(
            x=x_enc,
            y=alt.Y("ACWR:Q", scale=alt.Scale(domain=[lo, hi], zero=False), title="ACWR"),
            tooltip=[
                alt.Tooltip(f"{date_col}:T", title=x_title, format="%Y-%m-%d"),
                alt.Tooltip("ACWR:Q", format=".2f"),
            ],
        )
    )
    rule_high = (
        alt.Chart(pd.DataFrame({"y": [1.5]}))
        .mark_rule(strokeDash=[4, 4], color="#e74c3c", opacity=0.7)
        .encode(y="y:Q")
    )
    st.altair_chart((band_layer + line + rule_high).properties(height=260), use_container_width=True)
    st.caption(
        "X-axis: calendar week starting Monday. "
        "Green band = sweet spot (0.8–1.3). Red dashed line = overload caution (~1.5). "
        "Distinct from rolling 7d÷28d acute_chronic_ratio used by HIGH_TRAINING_LOAD flags."
    )


def _pace_weekly_df(workload_pace: dict[str, Any] | None, domain_key: str):
    """Date-indexed DataFrame of calendar-week rollups for a pace domain."""
    if not workload_pace:
        return None
    domain = workload_pace.get(domain_key)
    if not isinstance(domain, dict):
        return None
    return _history_df(domain.get("weekly_rollups") or [], "week_start")


def _render_pace_charts(
    workload_pace: dict[str, Any] | None,
    *,
    domain_key: str,
    title: str,
    load_label: str,
) -> None:
    """WoW change + load vs 4-week average charts for one pace domain."""
    df = _pace_weekly_df(workload_pace, domain_key)
    if df is None or getattr(df, "empty", True):
        st.caption(f"No {title.lower()} history yet.")
        return
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown(f"**{title} · week-over-week %**")
            _time_chart(
                df,
                {"wow_change_pct": "WoW %"},
                grain="week",
                y_title="% change vs prior week",
            )
            st.caption("Chart is Mon–Sun calendar weeks. Pace lights use rolling 7-day windows through today.")
    with right:
        with st.container(border=True):
            st.markdown(f"**{title} · weekly load vs 4-wk avg**")
            _time_chart(
                df,
                {
                    "load": f"This week ({load_label})",
                    "four_week_avg_load": "4-wk avg",
                },
                grain="week",
                y_title=load_label,
            )
            st.caption("Points are Mon–Sun calendar weeks; the current week is in-progress until Sunday.")


def _latest_and_delta(df, col: str, lookback: int = 7) -> tuple[float | None, float | None]:
    """Most recent value of ``col`` and its change vs ~``lookback`` points prior."""
    if df is None or getattr(df, "empty", True) or col not in df.columns:
        return (None, None)
    series = df[col].dropna()
    if series.empty:
        return (None, None)
    latest = float(series.iloc[-1])
    if len(series) == 1:
        return (latest, None)
    prior = float(series.iloc[-min(lookback + 1, len(series))])
    return (latest, latest - prior)


def _headline_metric(
    column,
    label: str,
    df,
    col: str,
    *,
    suffix: str = "",
    lookback: int = 7,
    higher_is_better: bool = True,
    fallback: Any = None,
) -> None:
    """A metric tile fed from trend history, with a delta vs a week ago."""
    latest, delta = _latest_and_delta(df, col, lookback=lookback)
    value = latest if latest is not None else fallback
    delta_str = None
    if delta is not None and abs(delta) >= 0.05:
        delta_str = f"{delta:+.1f}{suffix}"
    color = "normal" if higher_is_better else "inverse"
    column.metric(label, _fmt(value, suffix=suffix), delta=delta_str, delta_color=color)


def _parse_target_number(target: Any) -> int | None:
    """Pull the first integer out of a target label like ``"3-4x"`` -> 3."""
    if target is None:
        return None
    import re

    found = re.findall(r"\d+", str(target))
    return int(found[0]) if found else None


def _goal_progress_row(name: str, leaf: dict) -> None:
    label = name.replace("_", " ").title()
    completed = leaf.get("completed")
    target_n = _parse_target_number(leaf.get("target"))
    _, status_label = _goal_status_meta(leaf.get("status"))
    if isinstance(completed, (int, float)) and target_n:
        fraction = max(0.0, min(1.0, completed / target_n))
        suffix = f" · {status_label}" if status_label else ""
        st.progress(fraction, text=f"{label}: {completed} / {leaf.get('target')}{suffix}")
    elif leaf.get("done") is not None:
        emoji = "✅" if leaf.get("done") else "⬜"
        st.markdown(f"{emoji} **{label}** — {status_label or ('Done' if leaf.get('done') else 'Not yet')}")
    else:
        st.markdown(f"**{label}** · {_goal_detail(leaf)}")


def _render_goal_progress(goals_status: dict) -> None:
    """Goal snapshot as progress bars (Overview), degrading to rows/badges."""
    for name, value in goals_status.items():
        if _is_leaf_goal(value):
            _goal_progress_row(name, value)
        elif isinstance(value, dict):
            st.markdown(f"**{name.replace('_', ' ').title()}**")
            for sub_name, sub in value.items():
                if isinstance(sub, dict):
                    _goal_progress_row(sub_name, sub)
                else:
                    st.markdown(f"&nbsp;&nbsp;• {sub_name.replace('_', ' ').title()}: {sub}")
        else:
            st.markdown(f"**{name.replace('_', ' ').title()}**: {value}")


def _render_auth_gate(cookie_manager) -> bool:
    """Return True when the user may proceed to the app."""
    from dashboard.auth import (
        auth_configured,
        sign_in_with_password,
        sign_up_with_password,
    )

    if st.session_state.get("auth_user_id"):
        return True

    if not auth_configured() or _fixture_mode_enabled():
        return True

    st.title("Soma — Sign in")
    st.caption(
        "Sign in with Supabase Auth. Every query runs under the `authenticated` "
        "role scoped to your user id, so row-level security isolates your data."
    )
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_ANON_KEY"].strip()

    def _try_sign_in(email: str, password: str) -> None:
        session = sign_in_with_password(
            email=email, password=password, supabase_url=url, anon_key=key
        )
        st.session_state.auth_user_id = session["user_id"]
        st.session_state.auth_email = session["email"]
        st.session_state.auth_token = session["access_token"]
        _store_refresh_cookie(cookie_manager, session.get("refresh_token", ""))
        st.rerun()

    if _cloud_dashboard():
        with st.form("cloud_signin_form", clear_on_submit=False):
            email = st.text_input("Email", key="signin_email", autocomplete="email")
            password = st.text_input(
                "Password", type="password", key="signin_pw", autocomplete="current-password"
            )
            submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            try:
                _try_sign_in(email, password)
            except Exception as exc:
                st.error(str(exc))
        st.info(
            "Self-service sign-up is disabled on the cloud dashboard. "
            "Create accounts in Supabase Dashboard → Authentication."
        )
        st.stop()

    tab_in, tab_up = st.tabs(["Sign in", "Create account"])

    with tab_in:
        with st.form("local_signin_form", clear_on_submit=False):
            email = st.text_input("Email", key="signin_email", autocomplete="email")
            password = st.text_input(
                "Password", type="password", key="signin_pw", autocomplete="current-password"
            )
            submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            try:
                _try_sign_in(email, password)
            except Exception as exc:
                st.error(str(exc))

    with tab_up:
        with st.form("local_signup_form", clear_on_submit=False):
            email2 = st.text_input("Email", key="signup_email", autocomplete="email")
            password2 = st.text_input(
                "Password", type="password", key="signup_pw", autocomplete="new-password"
            )
            signup_submitted = st.form_submit_button("Create account", use_container_width=True)
        if signup_submitted:
            try:
                session = sign_up_with_password(
                    email=email2, password=password2, supabase_url=url, anon_key=key
                )
                st.session_state.auth_user_id = session["user_id"]
                st.session_state.auth_email = session["email"]
                st.session_state.auth_token = session["access_token"]
                _store_refresh_cookie(cookie_manager, session.get("refresh_token", ""))
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.info(
        "Operator mode: set SOMA_USER_ID + SOMA_DATABASE_URL without Supabase keys, "
        "or SOMA_DASHBOARD_FIXTURE=1 for demo data."
    )
    st.stop()


_RANGE_OPTIONS: dict[str, int] = {"14d": 14, "30d": 30, "90d": 90, "6mo": 180}


def _render_sidebar(
    mode: str, ctx: dict, guidelines: GuidelinesContext | None, cookie_manager=None
) -> int:
    """Render the sidebar and return the selected chart window (in days)."""
    with st.sidebar:
        st.markdown("## 🧬 Soma")
        st.caption("Personal Health OS")
        mode_badge = "🧪 Demo data" if mode == "fixture" else "🔒 Live · RLS-scoped"
        st.caption(mode_badge)
        st.divider()

        if st.session_state.get("auth_email"):
            st.caption(f"Signed in as **{st.session_state.auth_email}**")
            if st.button("Sign out", use_container_width=True):
                _clear_refresh_cookie(cookie_manager)
                # Block cookie-based restore for the rest of this session in case
                # the (already-mounted) cookie component reports a stale value on
                # the rerun before the browser finishes clearing it. A real page
                # reload drops this flag — and by then the cookie is gone.
                st.session_state._signed_out = True
                for key in (
                    "auth_user_id",
                    "auth_email",
                    "auth_token",
                    "chat_messages",
                    "chat_sessions",
                    "active_chat_session",
                    "chat_session_counter",
                    "chat_session_select",
                    "_last_query_results",
                    "_coaching_saved",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
        elif mode == "live":
            uid = ctx.get("user_id", "")
            st.caption(f"User: `{uid[:8]}…`")
        if mode == "live" and st.button("↻ Refresh data", use_container_width=True):
            _load_live_context.clear()
            _load_history_live.clear()
            st.rerun()

        st.divider()
        st.markdown("**📈 Chart range**")
        choice = st.segmented_control(
            "Chart range",
            list(_RANGE_OPTIONS),
            default="30d",
            key="chart_range",
            label_visibility="collapsed",
        )
        days = _RANGE_OPTIONS.get(choice or "30d", 30)

        if guidelines and guidelines.has_content():
            with st.expander("Personal context"):
                if guidelines.injury_history:
                    st.markdown("**Injury history**")
                    st.caption(guidelines.injury_history[:400])
                if guidelines.my_goals:
                    st.markdown("**Goals**")
                    st.caption(guidelines.my_goals[:400])

        st.divider()
        st.toggle(
            "Developer mode",
            key="dev_mode",
            help="Show raw JSON context and tool-call payloads for debugging.",
        )
    return days


def _render_top_header(ctx: dict, mode: str) -> None:
    """Shared branding header shown above the page navigation."""
    as_of = str(ctx.get("as_of", date.today().isoformat()))[:10]
    left, right = st.columns([4, 1], vertical_alignment="center")
    with left:
        st.markdown("## 🧬 Soma")
        st.caption("Your personal health snapshot")
    with right:
        badge = "🧪 Demo" if mode == "fixture" else "🔒 Live"
        st.markdown(
            f"<div style='text-align:right'>📅 <b>{as_of}</b><br>"
            f"<span style='color:#64748b;font-size:0.85em'>{badge}</span></div>",
            unsafe_allow_html=True,
        )
    st.divider()


def _active_alerts(ctx: dict) -> list[str]:
    """Merge briefing flags + recent anomalies into short human-readable chips."""
    chips: list[str] = []
    for flag in (ctx.get("briefing") or {}).get("flags") or []:
        chips.append(str(flag).replace("_", " ").title())
    for row in ctx.get("recent_anomalies") or []:
        sev = _SEVERITY_EMOJI.get(str(row.get("severity", "")).lower(), "•")
        chips.append(f"{sev} {row.get('metric')}")
    return chips


def _render_hero(ctx: dict, mdf, fdf) -> None:
    """Top-of-page priority strip: readiness, focus, alerts, trend deltas."""
    features = ctx.get("features") or {}
    readiness = features.get("overall_readiness_score")
    r_latest, _ = _latest_and_delta(fdf, "overall_readiness_score")
    readiness = r_latest if r_latest is not None else readiness
    emoji, label = _readiness_meta(readiness)

    left, right = st.columns([1, 2], vertical_alignment="center")
    with left:
        with st.container(border=True):
            st.markdown("**Readiness**")
            st.markdown(f"<h1 style='margin:0'>{emoji} {_fmt(readiness)}</h1>", unsafe_allow_html=True)
            if label:
                st.caption(f"{label} · scored /100")
            if isinstance(readiness, (int, float)):
                st.progress(max(0.0, min(1.0, readiness / 100)))
    with right:
        with st.container(border=True):
            st.markdown("**🎯 Today's focus**")
            st.info(ctx.get("todays_focus") or "No focus computed yet.")
            _render_training_phases(ctx.get("training_phase"), compact=True)
            _render_workload_pace_lights(ctx.get("workload_pace"))
            chips = _active_alerts(ctx)
            if chips:
                st.markdown("🚨 " + " &nbsp; ".join(f"`{c}`" for c in chips))
            else:
                st.caption("No active alerts ✅")

    st.markdown("###### Trends at a glance · vs ~1 week ago")
    cols = st.columns(5)
    hrv_fallback = (ctx.get("today_metrics") or {}).get("hrv_rmssd")
    _headline_metric(cols[0], "HRV", mdf, "hrv_rmssd", suffix=" ms", fallback=hrv_fallback)
    if (mdf is None or getattr(mdf, "empty", True)
            or "hrv_rmssd" not in getattr(mdf, "columns", [])
            or mdf["hrv_rmssd"].dropna().empty) and hrv_fallback is None:
        cols[0].caption(
            "No HRV in Soma yet — confirm samples in Apple Health → Heart → HRV."
        )
    _headline_metric(cols[1], "Sleep", mdf, "sleep_hours", suffix=" h",
                     fallback=(ctx.get("today_metrics") or {}).get("sleep_hours"))
    _headline_metric(cols[2], "Resting HR", mdf, "resting_hr", suffix=" bpm",
                     higher_is_better=False, fallback=(ctx.get("today_metrics") or {}).get("resting_hr"))
    _headline_metric(cols[3], "Weight", mdf, "body_weight_lbs", suffix=" lb", higher_is_better=False)
    _headline_metric(cols[4], "Readiness", fdf, "overall_readiness_score", fallback=readiness)


def _render_alerts_row(ctx: dict) -> None:
    with st.container(border=True):
        st.markdown("**🚨 Recent anomalies**")
        anomalies = ctx.get("recent_anomalies") or []
        if anomalies:
            for row in anomalies:
                sev = _SEVERITY_EMOJI.get(str(row.get("severity", "")).lower(), "•")
                st.markdown(f"{sev} **{row.get('date')}** · {row.get('metric')}")
                if row.get("description"):
                    st.caption(row["description"])
        else:
            st.caption("No anomalies detected ✅")


def _tab_overview(ctx: dict, mdf, fdf) -> None:
    left, right = st.columns([2, 3])
    with left:
        with st.container(border=True):
            st.markdown("**📝 Latest briefing**")
            briefing = ctx.get("briefing") or {}
            st.write(briefing.get("coaching_note") or "_No briefing available yet._")
            if briefing.get("flags"):
                st.caption("Flags: " + " ".join(f"`{f}`" for f in briefing["flags"]))
        with st.container(border=True):
            st.markdown("**🎯 Goal progress**")
            goals = ctx.get("goals_status")
            if goals:
                _render_goal_progress(goals)
            else:
                st.caption("No goal snapshot yet.")
            mileage = ctx.get("mileage_check")
            if isinstance(mileage, dict) and mileage.get("this_week_miles") is not None:
                this_wk, last_wk = mileage.get("this_week_miles"), mileage.get("last_week_miles")
                delta = None
                if isinstance(this_wk, (int, float)) and isinstance(last_wk, (int, float)):
                    delta = f"{this_wk - last_wk:+.1f} mi vs last week"
                st.metric("🏃 Weekly mileage", _fmt(this_wk, suffix=" mi"), delta=delta)
        _render_training_phases(ctx.get("training_phase"), compact=False)
        _render_workload_pace_lights(ctx.get("workload_pace"))
        # Compact streak glance — full month grids live on the Training tab.
        mode = "fixture" if _fixture_mode_enabled() else "live"
        cal = _load_workout_calendar(ctx, mode)
        with st.container(border=True):
            st.markdown("**🔥 Workout streak**")
            sc = st.columns(2)
            sc[0].metric("Current", f"{cal.get('current_streak', 0)} days")
            sc[1].metric("Longest", f"{cal.get('longest_streak', 0)} days")
            st.caption("Lifting + cardio + Fitbit · see Training for the month calendar.")
    with right:
        with st.container(border=True):
            st.markdown("**Readiness · by day**")
            _time_chart(
                fdf,
                {"overall_readiness_score": "Readiness"},
                grain="day",
                y_title="Score /100",
            )
        with st.container(border=True):
            st.markdown("**Lifting volume · by calendar week**")
            lifting_df = _pace_weekly_df(ctx.get("workload_pace"), "lifting")
            _time_chart(
                lifting_df,
                {"load": "Volume (lb)"},
                grain="week",
                empty="No lifting weeks yet.",
                y_title="lb",
            )
            st.caption("Each point = Mon–Sun week. Current week is in-progress until Sunday.")
        with st.container(border=True):
            st.markdown("**Cardio minutes · by calendar week**")
            cardio_df = _pace_weekly_df(ctx.get("workload_pace"), "cardio")
            _time_chart(
                cardio_df,
                {"load": "Minutes"},
                grain="week",
                empty="No cardio weeks yet.",
                y_title="min",
            )
            st.caption(
                "Each point = Mon–Sun week (excludes Apple Health strength-typed workouts)."
            )
    st.divider()
    _render_alerts_row(ctx)


def _tab_recovery(ctx: dict, mdf, fdf) -> None:
    m = ctx.get("today_metrics") or {}
    c = st.columns(4)
    _headline_metric(c[0], "HRV (rMSSD)", mdf, "hrv_rmssd", suffix=" ms", fallback=m.get("hrv_rmssd"))
    if not _series_has_data(mdf, "hrv_rmssd") and m.get("hrv_rmssd") is None:
        c[0].caption("No HRV samples in Soma — see Apple Health → Heart → HRV.")
    _headline_metric(c[1], "Resting HR", mdf, "resting_hr", suffix=" bpm", higher_is_better=False,
                     fallback=m.get("resting_hr"))
    _headline_metric(c[2], "Weight", mdf, "body_weight_lbs", suffix=" lb", higher_is_better=False)
    _headline_metric(c[3], "Body fat", mdf, "body_fat_pct", suffix=" %", higher_is_better=False)

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**HRV · by day**")
            _padded_chart(
                mdf,
                {"hrv_rmssd": "HRV", "hrv_7d_avg": "7-day avg"},
                pad=5,
                grain="day",
                y_title="ms",
                empty="No HRV samples in Soma yet. Check Apple Health → Heart → HRV, then HAE export.",
            )
        with st.container(border=True):
            st.markdown("**Body weight · by day**")
            _padded_chart(mdf, {"body_weight_lbs": "Weight (lb)"}, pad=5, grain="day", y_title="lb")
    with right:
        with st.container(border=True):
            st.markdown("**Resting heart rate · by day**")
            _padded_chart(mdf, {"resting_hr": "Resting HR"}, pad=5, grain="day", y_title="bpm")
        with st.container(border=True):
            st.markdown("**Blood oxygen (SpO₂) · by day**")
            if _series_has_data(mdf, "spo2_pct"):
                _padded_chart(mdf, {"spo2_pct": "SpO₂ %"}, pad=2, grain="day", y_title="%")
            else:
                st.caption("No SpO₂ in this range — not synced from Health yet.")


def _cardio_mode_totals(ctx: dict, mode: str) -> dict[str, dict[str, float]]:
    """Calendar-week (Mon–as_of) running vs cycling totals for the Training tab."""
    from pipeline.mileage_ramp import iso_week_start

    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    week_start = iso_week_start(as_of)
    days = (as_of - week_start).days + 1
    if mode == "fixture":
        rows = [
            r
            for r in _fixture_cardio_events(as_of)
            if week_start
            <= date.fromisoformat(str(r["event_date"])[:10])
            <= as_of
        ]
        return summarize_cardio_by_mode(rows)
    try:
        with _scoped_conn(ctx["user_id"]) as conn:
            rows = fetch_cardio_events_window(
                conn, user_id=ctx["user_id"], as_of=as_of, days=days
            )
        return summarize_cardio_by_mode(rows)
    except Exception:
        return summarize_cardio_by_mode([])


def _format_phase_type(phase_type: Any) -> str:
    text = str(phase_type or "").strip()
    return text.replace("_", " ").title() if text else ""


def _render_training_phases(phase_ctx: dict[str, Any] | None, *, compact: bool = False) -> None:
    """Show active and upcoming training blocks from dashboard context."""
    if not phase_ctx:
        if not compact:
            st.caption("No training phases scheduled — ask Coaching chat to add one.")
        return

    active = phase_ctx.get("active")
    upcoming = phase_ctx.get("upcoming") or []

    if compact:
        if isinstance(active, dict):
            name = active.get("name") or "Training block"
            weeks_left = active.get("weeks_remaining")
            end_date = active.get("end_date")
            bits = [_format_phase_type(active.get("phase_type"))]
            if weeks_left is not None:
                bits.append(f"{weeks_left} week(s) left")
            if end_date:
                bits.append(f"through {end_date}")
            st.markdown(f"📅 **{name}** · {' · '.join(bits)}")
        elif upcoming:
            nxt = upcoming[0] if isinstance(upcoming[0], dict) else {}
            st.markdown(
                f"📅 Next block: **{nxt.get('name', 'Scheduled phase')}** "
                f"starts {nxt.get('start_date', '?')}"
            )
        return

    with st.container(border=True):
        st.markdown("**📅 Training schedule**")
        if isinstance(active, dict):
            name = active.get("name") or "Training block"
            st.markdown(f"**Active — {name}**")
            meta_bits = [_format_phase_type(active.get("phase_type"))]
            if active.get("start_date") and active.get("end_date"):
                meta_bits.append(f"{active['start_date']} → {active['end_date']}")
            if active.get("weeks_remaining") is not None:
                meta_bits.append(f"{active['weeks_remaining']} week(s) remaining")
            st.caption(" · ".join(meta_bits))
            pct = active.get("pct_complete")
            if isinstance(pct, (int, float)):
                st.progress(min(1.0, max(0.0, pct / 100.0)))
                st.caption(f"{pct:.0f}% through this block")
            if active.get("target_notes"):
                st.caption(f"Targets: {active['target_notes']}")
            if active.get("notes"):
                st.write(active["notes"])
        else:
            st.caption("No active training block right now.")

        if upcoming:
            st.markdown("**Upcoming**")
            for phase in upcoming:
                if not isinstance(phase, dict):
                    continue
                label = phase.get("name") or "Scheduled phase"
                ptype = _format_phase_type(phase.get("phase_type"))
                dates = ""
                if phase.get("start_date") and phase.get("end_date"):
                    dates = f" · {phase['start_date']} → {phase['end_date']}"
                st.markdown(f"- **{label}** ({ptype}){dates}")
                if phase.get("notes"):
                    st.caption(phase["notes"])


def _render_exercise_progress(strength_progress: dict[str, Any] | None) -> None:
    if not strength_progress:
        st.caption("No strength analytics yet.")
        return
    exercises = strength_progress.get("top_exercises") or []
    series_map = strength_progress.get("exercise_series") or {}
    if not exercises:
        st.caption("Log Hevy workouts to see per-exercise trends.")
        return
    names = [str(ex.get("exercise_name")) for ex in exercises if ex.get("exercise_name")]
    if not names:
        return
    selected = st.selectbox("Exercise", names, key="strength_exercise_select")
    series = series_map.get(selected) or []
    if not series:
        st.caption("No sessions for this exercise in the lookback window.")
        return
    sdf = _history_df(series, "event_date")
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown(f"**{selected} · top working weight · by session day**")
            _chart(sdf, {"top_weight_lbs": "Top weight (lb)"}, grain="day", y_title="lb")
    with right:
        with st.container(border=True):
            st.markdown(f"**{selected} · session volume · by session day**")
            _chart(sdf, {"volume_lbs": "Volume (lb)"}, grain="day", kind="area", y_title="lb")
    latest = exercises[[ex.get("exercise_name") for ex in exercises].index(selected)]
    delta_weight = latest.get("weight_delta_vs_prior")
    delta_vol = latest.get("volume_change_pct_vs_prior")
    bits: list[str] = []
    if isinstance(delta_weight, (int, float)):
        bits.append(f"weight {delta_weight:+.1f} lb vs prior session")
    if isinstance(delta_vol, (int, float)):
        bits.append(f"volume {delta_vol:+.1f}% vs prior session")
    if bits:
        st.caption("Latest session: " + " · ".join(bits))


@st.cache_data(ttl=60)
def _load_workout_calendar_live(user_id: str, as_of_iso: str) -> dict[str, Any]:
    as_of = date.fromisoformat(as_of_iso)
    with _scoped_conn(user_id) as conn:
        return fetch_workout_calendar(
            conn,
            user_id=user_id,
            as_of=as_of,
            include_previous_month=True,
        )


def _load_workout_calendar(ctx: dict, mode: str) -> dict[str, Any]:
    """Current + previous month workout calendar (fixture or live DB)."""
    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    if mode == "fixture":
        return build_workout_calendar(
            _fixture_strength_events(as_of),
            _fixture_cardio_events(as_of),
            as_of=as_of,
            include_previous_month=True,
        )
    try:
        return _load_workout_calendar_live(ctx["user_id"], as_of.isoformat())
    except Exception:
        return build_workout_calendar([], [], as_of=as_of, include_previous_month=True)


def _calendar_status(cell: Mapping[str, Any]) -> str:
    """Bucket a grid cell into a legend / color category."""
    if cell.get("future") or not cell.get("in_month"):
        return "Out of range"
    if not cell.get("worked_out"):
        return "Rest"
    kind = str(cell.get("kind") or "")
    if kind == "both":
        return "Lift + cardio"
    if kind == "lifting":
        return "Lifting"
    if cell.get("fitbit") and not cell.get("lifting"):
        return "Fitbit / walk"
    return "Cardio"


def _render_month_calendar_chart(month: Mapping[str, Any]) -> None:
    """Altair month grid: Mon–Sun columns, one row per week."""
    import altair as alt
    import pandas as pd

    grid = list(month.get("grid") or [])
    if not grid:
        st.caption("No calendar days.")
        return

    rows: list[dict[str, Any]] = []
    for cell in grid:
        status = _calendar_status(cell)
        rows.append(
            {
                "weekday": int(cell["weekday"]),
                "weekday_name": cell["weekday_name"],
                "week_index": int(cell["week_index"]),
                "day_label": str(cell["day"]) if cell.get("in_month") else "",
                "status": status,
                "tooltip_date": cell.get("date_iso"),
                "tooltip_detail": cell.get("label") or status,
                "in_month": bool(cell.get("in_month")),
            }
        )
    df = pd.DataFrame(rows)
    color_scale = alt.Scale(
        domain=["Rest", "Lifting", "Cardio", "Lift + cardio", "Fitbit / walk", "Out of range"],
        range=["#e8e8e8", "#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#f3f4f6"],
    )
    chart = (
        alt.Chart(df)
        .mark_rect(stroke="#ffffff", strokeWidth=2, cornerRadius=3)
        .encode(
            x=alt.X(
                "weekday:O",
                title=None,
                axis=alt.Axis(
                    labelExpr="['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][datum.value]",
                    labelAngle=0,
                    ticks=False,
                    domain=False,
                ),
            ),
            y=alt.Y("week_index:O", title=None, axis=None),
            color=alt.Color(
                "status:N",
                scale=color_scale,
                legend=alt.Legend(title=None, orient="bottom", columns=3),
            ),
            tooltip=[
                alt.Tooltip("tooltip_date:N", title="Date"),
                alt.Tooltip("status:N", title="Status"),
                alt.Tooltip("tooltip_detail:N", title="Activities"),
            ],
        )
        .properties(height=max(140, 28 * (df["week_index"].nunique() + 1)))
    )
    labels = (
        alt.Chart(df[df["in_month"]])
        .mark_text(fontSize=11, fontWeight=500)
        .encode(
            x="weekday:O",
            y="week_index:O",
            text="day_label:N",
            color=alt.value("#111827"),
        )
    )
    st.altair_chart(chart + labels, use_container_width=True)


def _render_workout_calendar(ctx: dict, mode: str) -> None:
    """Training-tab section: streak metrics + current/previous month grids."""
    calendar = _load_workout_calendar(ctx, mode)
    st.markdown("###### Workout calendar · lifting + cardio + Fitbit")
    streak_cols = st.columns(3)
    streak_cols[0].metric("🔥 Current streak", f"{calendar.get('current_streak', 0)} days")
    streak_cols[1].metric("🏅 Longest streak", f"{calendar.get('longest_streak', 0)} days")
    streak_cols[2].metric(
        "📅 Active days (window)",
        f"{calendar.get('workout_days_count', 0)}",
        help="Distinct days with lifting, cardio, or Fitbit/Health Sync activity "
        "from the start of last month through today.",
    )
    months = list(calendar.get("months") or [])
    if not months:
        st.caption("No workout days in this window yet.")
        return
    # Render previous month then current month left→right when both present.
    cols = st.columns(len(months))
    for col, month in zip(cols, months):
        with col:
            with st.container(border=True):
                st.markdown(
                    f"**{month.get('label')}** · {month.get('workout_day_count', 0)} workout days"
                )
                _render_month_calendar_chart(month)
    st.caption(
        "One calendar for Hevy lifts, Apple Health / Strava cardio, and Fitbit "
        "(Health Sync) activities. Apple strength mirrors are ignored on Hevy days."
    )


def _tab_training(ctx: dict, fdf, wdf, mode: str) -> None:
    strength_progress = ctx.get("strength_progress") or {}
    workload_pace = ctx.get("workload_pace") or {}
    phase_ctx = ctx.get("training_phase")
    lifting = workload_pace.get("lifting") if isinstance(workload_pace.get("lifting"), dict) else {}
    cardio = workload_pace.get("cardio") if isinstance(workload_pace.get("cardio"), dict) else {}

    _render_training_phases(phase_ctx, compact=False)
    _render_workload_pace_lights(workload_pace)
    _render_workout_calendar(ctx, mode)

    c = st.columns(4)
    c[0].metric("Lifting · last 7d", _fmt(lifting.get("acute_load"), suffix=" lb"))
    c[1].metric("Cardio · last 7d", _fmt(cardio.get("acute_load"), suffix=" min"))

    def _acwr_txt(domain: Mapping[str, Any]) -> str:
        v = domain.get("acwr")
        return f"{v:.2f}" if isinstance(v, (int, float)) else "—"

    c[2].metric(
        "Lifting ACWR",
        _acwr_txt(lifting),
        help="Last 7 days ÷ average of the prior four 7-day windows. Yellow/red = overload only; underload stays green.",
    )
    c[3].metric(
        "Cardio ACWR",
        _acwr_txt(cardio),
        help="Last 7 days ÷ average of the prior four 7-day windows. Yellow/red = overload only; underload stays green.",
    )

    wow = strength_progress.get("week_over_week_change_pct")
    week_vol = strength_progress.get("this_week_volume_lbs")
    if week_vol is not None:
        st.metric(
            "Calendar-week lifting volume",
            _fmt(week_vol, suffix=" lb"),
            delta=f"{wow:+.1f}% vs last week" if isinstance(wow, (int, float)) else None,
            delta_color="off",
        )
    for flag in strength_progress.get("progress_flags") or []:
        if isinstance(flag, dict) and flag.get("message"):
            st.warning(flag["message"])

    totals = _cardio_mode_totals(ctx, mode)
    run_t, bike_t = totals["running"], totals["cycling"]
    st.markdown("###### Cardio by activity · this calendar week")
    mc = st.columns(2)
    mc[0].metric(
        "🏃 Running (this week)",
        _fmt(run_t["miles"], suffix=" mi"),
        delta=f"{_fmt(run_t['minutes'])} min · {int(run_t['sessions'])} sessions",
        delta_color="off",
    )
    mc[1].metric(
        "🚴 Cycling (this week)",
        _fmt(bike_t["miles"], suffix=" mi"),
        delta=f"{_fmt(bike_t['minutes'])} min · {int(bike_t['sessions'])} sessions",
        delta_color="off",
    )

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Cardio minutes · by calendar week**")
            _chart(
                _pace_weekly_df(workload_pace, "cardio"),
                {"load": "Minutes"},
                grain="week",
                empty="No cardio weeks yet.",
                y_title="min",
            )
        with st.container(border=True):
            st.markdown("**Lifting volume · by calendar week**")
            _chart(
                _pace_weekly_df(workload_pace, "lifting"),
                {"load": "Volume (lb)"},
                grain="week",
                empty="No lifting weeks yet.",
                y_title="lb",
            )
    with right:
        with st.container(border=True):
            st.markdown("**Cardio ACWR · by calendar week**")
            _acwr_band_chart(_pace_weekly_df(workload_pace, "cardio"))
        with st.container(border=True):
            st.markdown("**Lifting ACWR · by calendar week**")
            _acwr_band_chart(_pace_weekly_df(workload_pace, "lifting"))

    st.markdown("###### Weekly activity · by calendar week (separate scales)")
    week_cols = st.columns(3)
    with week_cols[0]:
        with st.container(border=True):
            st.markdown("**Strength sessions · by calendar week**")
            _chart(
                wdf,
                {"strength_sessions": "Sessions"},
                grain="week",
                empty="No weekly summaries yet.",
                y_title="sessions",
            )
    with week_cols[1]:
        with st.container(border=True):
            st.markdown("**Running miles · by calendar week**")
            _chart(
                wdf,
                {"running_miles": "Miles"},
                grain="week",
                empty="No weekly summaries yet.",
                y_title="mi",
            )
    with week_cols[2]:
        with st.container(border=True):
            st.markdown("**Cardio minutes · by calendar week**")
            _chart(
                wdf,
                {"cardio_minutes": "Minutes"},
                grain="week",
                empty="No weekly summaries yet.",
                y_title="min",
            )

    st.markdown("###### Training pace · by calendar week")
    pace_row = st.columns(2)
    with pace_row[0]:
        _render_pace_charts(workload_pace, domain_key="lifting", title="Lifting", load_label="lb")
    with pace_row[1]:
        _render_pace_charts(workload_pace, domain_key="cardio", title="Cardio", load_label="min")

    st.markdown("###### Running & cycling · by calendar week")
    cardio_pace_row = st.columns(2)
    with cardio_pace_row[0]:
        _render_pace_charts(workload_pace, domain_key="running", title="Running", load_label="mi")
    with cardio_pace_row[1]:
        _render_pace_charts(workload_pace, domain_key="cycling", title="Cycling", load_label="mi")

    st.markdown("###### Lifting progression · by calendar week")
    prog_left, prog_right = st.columns(2)
    weekly_df = _history_df(strength_progress.get("weekly_rollups") or [], "week_start")
    focus_df = _history_df(strength_progress.get("focus_weekly") or [], "week_start")
    with prog_left:
        with st.container(border=True):
            st.markdown("**Lifting volume · by calendar week**")
            _chart(
                weekly_df,
                {"volume_lbs": "Total volume"},
                grain="week",
                y_title="lb",
            )
            st.caption("Calendar-week lifting volume (pace lights use rolling 7-day windows).")
    with prog_right:
        with st.container(border=True):
            st.markdown("**Upper vs lower volume · by calendar week**")
            _chart(
                focus_df,
                {
                    "upper_volume_lbs": "Upper",
                    "lower_volume_lbs": "Lower",
                },
                grain="week",
                empty="No focus split yet.",
                y_title="lb",
            )

    with st.container(border=True):
        st.markdown("**Per-exercise trends**")
        _render_exercise_progress(strength_progress)

    if mode == "live":
        with st.expander("Schedule a training phase"):
            st.caption(
                "Multi-week blocks (building, deload, fat loss, running focus). "
                "You can also ask Coaching chat to set one."
            )
            with st.form("training_phase_form"):
                name = st.text_input("Name", placeholder="6-week hypertrophy block")
                phase_type = st.selectbox(
                    "Phase type",
                    ["building", "deload", "fat_loss", "running", "maintenance", "custom"],
                )
                col_a, col_b = st.columns(2)
                start = col_a.date_input("Start", value=date.today())
                end = col_b.date_input("End", value=date.today() + timedelta(weeks=6))
                notes = st.text_area("Notes", placeholder="Optional context for this block")
                target_notes = st.text_area(
                    "Targets",
                    placeholder="e.g. 3–4 lifts/week, cap weekly volume jumps at 10%",
                )
                if st.form_submit_button("Save phase"):
                    try:
                        from pipeline.training_phase import training_phase_row
                        from pipeline import persistence

                        row = training_phase_row(
                            user_id=ctx["user_id"],
                            name=name,
                            phase_type=phase_type,
                            start_date=start,
                            end_date=end,
                            notes=notes or None,
                            target_notes=target_notes or None,
                        )
                        with _scoped_conn(ctx["user_id"], read_only=False) as conn:
                            with conn.cursor() as cur:
                                persistence.insert_training_phase(cur, row)
                            conn.commit()
                        _load_live_context.clear()
                        st.success(f"Saved phase: {name}")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    if mode == "live":
        from pipeline.dashboard_queries import fetch_cardio_breakdown_7d
        from pipeline.mileage_ramp import iso_week_start

        as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
        week_start = iso_week_start(as_of)
        days = (as_of - week_start).days + 1
        with _scoped_conn(ctx["user_id"]) as conn:
            breakdown = fetch_cardio_breakdown_7d(
                conn, user_id=ctx["user_id"], as_of=as_of, days=days
            )
        with st.expander("Cardio breakdown by source (this calendar week)"):
            if breakdown:
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
            else:
                st.caption("No cardio_events in this calendar week yet.")


def _tab_sleep(ctx: dict, mdf, fdf) -> None:
    m = ctx.get("today_metrics") or {}
    c = st.columns(4)
    _headline_metric(c[0], "Last night", mdf, "sleep_hours", suffix=" h", fallback=m.get("sleep_hours"))
    _headline_metric(c[1], "7-day avg", mdf, "sleep_7d_avg", suffix=" h")
    _headline_metric(c[2], "Sleep score", mdf, "sleep_score")
    _headline_metric(c[3], "Sleep debt (7d)", fdf, "sleep_debt_7d", suffix=" h", higher_is_better=False)

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Sleep duration · by day**")
            _padded_chart(
                mdf,
                {"sleep_hours": "Sleep (h)", "sleep_7d_avg": "7-day avg"},
                pad=1,
                grain="day",
                y_title="h",
            )
            st.caption("X-axis is each night. The 7-day avg line is a trailing average of nightly hours.")
        with st.container(border=True):
            st.markdown("**Sleep stages · by night**")
            _sleep_stages_chart(mdf)
    with right:
        with st.container(border=True):
            st.markdown("**Sleep score · by day**")
            if _series_has_data(mdf, "sleep_score"):
                _padded_chart(mdf, {"sleep_score": "Sleep score"}, pad=5, grain="day", y_title="score")
            else:
                st.caption("Sleep score appears after nightly rollup has enough signals.")
        with st.container(border=True):
            st.markdown("**Sleep debt · by day**")
            _chart(
                fdf,
                {"sleep_debt_7d": "Sleep debt (h)"},
                grain="day",
                kind="area",
                y_title="h",
            )
            st.caption("X-axis is each day; the value is cumulative sleep debt over the prior 7 nights.")


def _history_query_all(user_id: str, mode: str) -> QueryAll:
    """Build the RLS-scoped read-only executor the chat's ``query_history`` tool uses."""

    def query_all(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if mode != "live":
            return [{"metric_date": "2026-06-01", "avg_sleep": 6.5}]
        from psycopg2.extras import RealDictCursor

        with _scoped_conn(user_id) as conn, conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    return query_all


def _render_query_details(query_results: list[dict[str, Any]]) -> None:
    """Show the SQL + rows behind the latest text-to-SQL answer, if any."""
    for result in query_results:
        if not result.get("ok") or not result.get("sql"):
            continue
        with st.expander("Query details"):
            st.code(result["sql"], language="sql")
            if result.get("rows"):
                st.dataframe(result["rows"], use_container_width=True, hide_index=True)


# Recent exchanges (user+assistant pairs) shown expanded; older ones collapse.
_CHAT_RECENT_TURNS = 3
_CHAT_STARTERS = (
    "How's my recovery looking?",
    "Am I ramping cardio too fast?",
    "Summarize this week's lifting.",
)


def _init_chat_sessions(user_id: str, mode: str) -> None:
    """Seed in-memory chat sessions, loading any persisted history into the first.

    Sessions are an in-memory UX grouping (``coaching_chat_messages`` has no
    session dimension), so persisted history rehydrates into the first chat only;
    additional chats live for the browser session. Writes still persist to the
    single DB stream.
    """
    if "chat_sessions" in st.session_state:
        return
    initial: list[dict[str, str]] = []
    if mode == "live":
        try:
            with _scoped_conn(user_id) as conn:
                initial = load_chat_messages(conn, user_id=user_id)
        except Exception:
            initial = []
    title = "Previous chat" if initial else "New chat"
    st.session_state.chat_sessions = {
        "session-1": {"title": title, "messages": initial, "persist": True}
    }
    st.session_state.active_chat_session = "session-1"
    st.session_state.chat_session_counter = 1
    st.session_state.chat_session_select = "session-1"


def _new_chat_session() -> None:
    """Start a blank ephemeral chat (not written to the shared DB stream)."""
    n = int(st.session_state.get("chat_session_counter", 1)) + 1
    st.session_state.chat_session_counter = n
    sid = f"session-{n}"
    st.session_state.chat_sessions[sid] = {
        "title": "New chat",
        "messages": [],
        "persist": False,
    }
    st.session_state.active_chat_session = sid
    # Keep selectbox widget state in sync (otherwise it overwrites active on next run).
    st.session_state.chat_session_select = sid
    st.session_state.pop("_last_query_results", None)
    st.session_state.pop("_coaching_saved", None)


def _on_chat_session_select() -> None:
    sid = st.session_state.get("chat_session_select")
    if isinstance(sid, str) and sid in st.session_state.get("chat_sessions", {}):
        st.session_state.active_chat_session = sid
        st.session_state.pop("_last_query_results", None)


def _active_chat_messages() -> list[dict[str, str]]:
    sessions = st.session_state.chat_sessions
    active = st.session_state.active_chat_session
    if active not in sessions:
        active = next(iter(sessions))
        st.session_state.active_chat_session = active
        st.session_state.chat_session_select = active
    return sessions[active]["messages"]


def _title_from_first_message(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= 36:
        return cleaned or "New chat"
    return f"{cleaned[:33].rstrip()}…"


def _render_chat_session_controls() -> None:
    sessions = st.session_state.chat_sessions
    ids = list(sessions)
    active = st.session_state.active_chat_session
    if active not in ids:
        active = ids[0]
        st.session_state.active_chat_session = active

    left, mid, right = st.columns([3, 1, 1], vertical_alignment="bottom")
    msgs = sessions[active]["messages"]
    with left:
        if len(ids) > 1:
            st.selectbox(
                "Chat",
                ids,
                index=ids.index(active),
                format_func=lambda sid: sessions[sid]["title"],
                key="chat_session_select",
                on_change=_on_chat_session_select,
                label_visibility="collapsed",
            )
        else:
            st.caption(sessions[active]["title"])
    with mid:
        if st.button("New chat", use_container_width=True, type="primary"):
            _new_chat_session()
            st.rerun()
    with right:
        if msgs and st.button("Clear", use_container_width=True, help="Clear this view only"):
            sessions[active]["messages"] = []
            sessions[active]["title"] = "New chat"
            st.session_state.pop("_last_query_results", None)
            st.rerun()
    if not sessions[active].get("persist", True):
        st.caption("This chat is local-only — it won’t merge into saved history.")
    elif msgs:
        st.caption("Clear empties this view; saved history reloads with the primary chat.")


def _render_chat_message(msg: dict) -> None:
    avatar = "🧑" if msg["role"] == "user" else "🧬"
    with st.chat_message(msg["role"], avatar=avatar):
        st.write(msg["content"])


def _render_chat_history(messages: list[dict[str, str]]) -> None:
    """Render the conversation oldest→newest; collapse older turns by default."""
    if not messages:
        return
    recent_count = _CHAT_RECENT_TURNS * 2
    if len(messages) > recent_count:
        older, recent = messages[:-recent_count], messages[-recent_count:]
        with st.expander(f"Earlier messages ({len(older)})", expanded=False):
            for msg in older:
                _render_chat_message(msg)
    else:
        recent = messages
    for msg in recent:
        _render_chat_message(msg)


def _render_chat_empty_state() -> str | None:
    """Empty-thread welcome + starter chips. Returns a starter click, if any."""
    st.markdown(
        "Ask about recovery, training load, sleep, or goals. "
        "Notes you ask Soma to remember are saved to your journal."
    )
    cols = st.columns(len(_CHAT_STARTERS))
    for col, starter in zip(cols, _CHAT_STARTERS, strict=True):
        with col:
            if st.button(starter, use_container_width=True, key=f"chat_starter:{starter}"):
                return starter
    return None


def _page_chat(ctx: dict, mode: str, guidelines: GuidelinesContext | None) -> None:
    st.markdown("#### Coaching chat")
    journal = ctx.get("athlete_journal") or []
    if journal:
        with st.expander(f"Journal ({len(journal)} notes)", expanded=False):
            for entry in journal[:12]:
                if not isinstance(entry, dict):
                    continue
                stamp = entry.get("entry_date") or "?"
                cat = entry.get("category") or "note"
                st.markdown(f"**{stamp}** · _{cat}_")
                st.caption(str(entry.get("body") or ""))

    saved_msg = st.session_state.pop("_coaching_saved", None)
    if saved_msg:
        st.success(f"Saved: {saved_msg}")

    user_id = ctx["user_id"]
    _init_chat_sessions(user_id, mode)
    _render_chat_session_controls()
    messages = _active_chat_messages()

    starter = None if messages else _render_chat_empty_state()
    _render_chat_history(messages)
    debug_tools = st.session_state.pop("_debug_tool_results", None)
    if debug_tools and _debug_enabled():
        with st.expander("🛠 Tool calls (developer — not saved)"):
            st.json(debug_tools)

    typed = st.chat_input("Message Soma…")
    user_input = starter or typed
    if not user_input:
        _render_query_details(st.session_state.get("_last_query_results") or [])
        return

    with st.spinner("Thinking…"):
        turn = run_coaching_turn(
            user_id=user_id,
            user_message=user_input,
            dashboard_context=ctx,
            messages=messages,
            llm=_resolve_llm(ctx),
            guidelines=guidelines,
            query_all=_history_query_all(user_id, mode),
        )
    st.session_state["_last_query_results"] = turn.get("query_results") or []
    if not messages:
        active = st.session_state.active_chat_session
        st.session_state.chat_sessions[active]["title"] = _title_from_first_message(user_input)
    messages.append({"role": "user", "content": user_input})
    messages.append({"role": "assistant", "content": turn["reply"]})
    pending = turn.get("pending_writes") or []
    active = st.session_state.active_chat_session
    should_persist = bool(
        mode == "live"
        and st.session_state.chat_sessions.get(active, {}).get("persist", True)
    )
    if pending and mode == "live":
        try:
            applied = _persist_coaching_writes(user_id, pending)
            if applied:
                _load_live_context.clear()
                st.session_state["_coaching_saved"] = "; ".join(applied)
        except Exception as exc:
            st.error(f"Failed to save coaching writes: {exc}")
            _render_chat_history(messages)
            _render_query_details(st.session_state.get("_last_query_results") or [])
            return
    elif pending and turn.get("tool_results") and _debug_enabled():
        st.session_state["_debug_tool_results"] = turn["tool_results"]

    if should_persist:
        try:
            with _scoped_conn(user_id, read_only=False) as conn:
                save_chat_messages(
                    conn,
                    user_id=user_id,
                    messages=[
                        ("user", user_input),
                        ("assistant", turn["reply"]),
                    ],
                )
                conn.commit()
        except Exception as exc:
            st.error(f"Failed to save chat: {exc}")
            _render_chat_history(messages)
            _render_query_details(st.session_state.get("_last_query_results") or [])
            return

    st.rerun()


def main() -> None:
    _load_dotenv()
    _apply_streamlit_secrets()
    st.set_page_config(page_title="Soma", layout="wide", initial_sidebar_state="expanded")

    cookie_manager = _cookie_manager()
    _restore_session_from_cookie(cookie_manager)
    _await_cookie_sync(cookie_manager)

    if not _render_auth_gate(cookie_manager):
        return

    mode = "fixture" if _fixture_mode_enabled() else "live"
    if mode == "fixture":
        ctx = _fixture_context()
    else:
        user_id = _session_user_id()
        if not user_id or not _resolve_db_url():
            st.error("Live mode requires SOMA_USER_ID (or sign-in) and SOMA_DATABASE_URL.")
            st.stop()
        try:
            ctx = _load_live_context(user_id, date.today().isoformat())
        except Exception as exc:
            st.error(f"Failed to load dashboard: {exc}")
            st.stop()

    guidelines = _load_guidelines(ctx["user_id"])
    days = _render_sidebar(mode, ctx, guidelines, cookie_manager)
    _render_top_header(ctx, mode)

    history = _load_history(ctx, mode, days)
    mdf = _enrich_metrics_frame(_history_df(history["metrics"], "metric_date"))
    fdf = _history_df(history["features"], "feature_date")
    wdf = _history_df(history["weekly"], "week_start")

    _render_hero(ctx, mdf, fdf)

    overview, recovery, training, sleep, coaching = st.tabs(
        ["📊 Overview", "❤️ Recovery", "🏋️ Training", "😴 Sleep", "💬 Coaching"]
    )
    with overview:
        _tab_overview(ctx, mdf, fdf)
    with recovery:
        _tab_recovery(ctx, mdf, fdf)
    with training:
        _tab_training(ctx, fdf, wdf, mode)
    with sleep:
        _tab_sleep(ctx, mdf, fdf)
    with coaching:
        _page_chat(ctx, mode, guidelines)

    if _debug_enabled():
        st.divider()
        with st.expander("🛠 Raw dashboard context (developer)"):
            st.code(json.dumps(ctx, indent=2, default=str), language="json")


if __name__ == "__main__":
    main()

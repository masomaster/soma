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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.coaching_chat import load_chat_messages, run_coaching_turn, save_chat_messages
from pipeline.dashboard_queries import (
    fetch_features_history,
    fetch_metrics_history,
    fetch_weekly_summaries,
    load_dashboard_context_from_db,
)
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


def _fixture_context() -> dict:
    from pipeline.dashboard_queries import build_dashboard_context

    today = date.today()
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
                "running": {
                    "interval": {"done": False, "status": "not_yet"},
                    "easy": {"done": True, "status": "done"},
                },
            },
            "todays_focus": "Strength session needed — 1 of 3-4x done",
            "mileage_check": {"flag": None, "this_week_km": 6.4, "last_week_km": 9.1},
        },
        weekly_summary={
            "week_start": today,
            "strength_sessions": 1,
            "running_km": 6.4,
            "cardio_minutes": 45,
        },
        provider_connections=[
            {"provider": "hevy", "status": "connected", "last_sync_at": "2026-06-19T10:00:00Z"},
            {"provider": "apple_health", "status": "connected", "last_sync_at": "2026-06-20T05:50:00Z"},
        ],
    )


def _fixture_guidelines() -> GuidelinesContext:
    return GuidelinesContext(
        my_goals="Build to 3-4 strength sessions per week. Long run Sundays.",
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
            }
        )
    return rows


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


def _load_history(ctx: dict, mode: str, days: int) -> dict[str, list[dict[str, Any]]]:
    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    if mode == "fixture":
        return {
            "metrics": _fixture_metrics_history(as_of, days),
            "features": _fixture_features_history(as_of, days),
            "weekly": _fixture_weekly_summaries(as_of, max(6, days // 7 + 1)),
        }
    try:
        return _load_history_live(ctx["user_id"], as_of.isoformat(), days)
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


def _cookie_manager():
    """Return a per-session CookieManager, or None if the component is missing."""
    try:
        import extra_streamlit_components as stx
    except ImportError:
        return None
    if "_cookie_manager" not in st.session_state:
        st.session_state._cookie_manager = stx.CookieManager(key="soma_cookies")
    return st.session_state._cookie_manager


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
        same_site="strict",
    )


def _clear_refresh_cookie(cookie_manager) -> None:
    if cookie_manager is None:
        return
    if cookie_manager.get(_REFRESH_COOKIE) is not None:
        cookie_manager.delete(_REFRESH_COOKIE, key="soma_del_refresh")


def _restore_session_from_cookie(cookie_manager) -> None:
    """Rehydrate auth state from the refresh-token cookie on a fresh page load."""
    from dashboard.auth import AuthError, auth_configured, refresh_session

    if cookie_manager is None or st.session_state.get("auth_user_id"):
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


_SYNC_STATUS_EMOJI = {
    "connected": "🟢",
    "ok": "🟢",
    "active": "🟢",
    "stale": "🟡",
    "degraded": "🟡",
    "error": "🔴",
    "disconnected": "🔴",
}

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


def _chart(df, series: dict[str, str], *, kind: str = "line", empty: str = "No data in this range yet.") -> None:
    """Render a chart for the present, non-empty columns, or a tidy caption."""
    if df is None or getattr(df, "empty", True):
        st.caption(empty)
        return
    present = {col: label for col, label in series.items() if col in df.columns and df[col].notna().any()}
    if not present:
        st.caption(empty)
        return
    data = df[list(present)].rename(columns=present)
    if kind == "area":
        st.area_chart(data, height=260)
    elif kind == "bar":
        st.bar_chart(data, height=260)
    else:
        st.line_chart(data, height=260)


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
        email = st.text_input("Email", key="signin_email")
        password = st.text_input("Password", type="password", key="signin_pw")
        if st.button("Sign in", key="signin_btn"):
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
        email = st.text_input("Email", key="signin_email")
        password = st.text_input("Password", type="password", key="signin_pw")
        if st.button("Sign in", key="signin_btn"):
            try:
                _try_sign_in(email, password)
            except Exception as exc:
                st.error(str(exc))

    with tab_up:
        email2 = st.text_input("Email", key="signup_email")
        password2 = st.text_input("Password", type="password", key="signup_pw")
        if st.button("Create account", key="signup_btn"):
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
                for key in ("auth_user_id", "auth_email", "auth_token", "chat_messages"):
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
            chips = _active_alerts(ctx)
            if chips:
                st.markdown("🚨 " + " &nbsp; ".join(f"`{c}`" for c in chips))
            else:
                st.caption("No active alerts ✅")

    st.markdown("###### Trends at a glance · vs ~1 week ago")
    cols = st.columns(5)
    _headline_metric(cols[0], "HRV", mdf, "hrv_rmssd", suffix=" ms",
                     fallback=(ctx.get("today_metrics") or {}).get("hrv_rmssd"))
    _headline_metric(cols[1], "Sleep", mdf, "sleep_hours", suffix=" h",
                     fallback=(ctx.get("today_metrics") or {}).get("sleep_hours"))
    _headline_metric(cols[2], "Resting HR", mdf, "resting_hr", suffix=" bpm",
                     higher_is_better=False, fallback=(ctx.get("today_metrics") or {}).get("resting_hr"))
    _headline_metric(cols[3], "Weight", mdf, "body_weight_lbs", suffix=" lb", higher_is_better=False)
    _headline_metric(cols[4], "Readiness", fdf, "overall_readiness_score", fallback=readiness)


def _render_alerts_row(ctx: dict) -> None:
    a1, a2 = st.columns(2)
    with a1:
        with st.container(border=True):
            st.markdown("**🔌 Sync health**")
            sync_rows = ctx.get("sync_health") or []
            if sync_rows:
                for row in sync_rows:
                    emoji = _SYNC_STATUS_EMOJI.get(str(row.get("status", "")).lower(), "⚪")
                    provider = str(row.get("provider") or "—").replace("_", " ").title()
                    last_sync = row.get("last_sync_at") or "never"
                    st.markdown(f"{emoji} **{provider}** — {row.get('status')} · last sync {last_sync}")
                    if row.get("last_error"):
                        st.caption(f"⚠️ {row['last_error']}")
            else:
                st.caption("No providers connected yet.")
    with a2:
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
            if isinstance(mileage, dict) and mileage.get("this_week_km") is not None:
                this_wk, last_wk = mileage.get("this_week_km"), mileage.get("last_week_km")
                delta = None
                if isinstance(this_wk, (int, float)) and isinstance(last_wk, (int, float)):
                    delta = f"{this_wk - last_wk:+.1f} km vs last week"
                st.metric("🏃 Weekly mileage", _fmt(this_wk, suffix=" km"), delta=delta)
    with right:
        with st.container(border=True):
            st.markdown("**🔥 Readiness trend**")
            _chart(fdf, {"overall_readiness_score": "Readiness"})
        with st.container(border=True):
            st.markdown("**🏃 Cardio load (rolling 7d)**")
            _chart(fdf, {"training_load_cardio_minutes_7d": "Cardio load 7d"}, kind="area")
    st.divider()
    _render_alerts_row(ctx)


def _tab_recovery(ctx: dict, mdf, fdf) -> None:
    m = ctx.get("today_metrics") or {}
    c = st.columns(4)
    _headline_metric(c[0], "HRV (rMSSD)", mdf, "hrv_rmssd", suffix=" ms", fallback=m.get("hrv_rmssd"))
    _headline_metric(c[1], "Resting HR", mdf, "resting_hr", suffix=" bpm", higher_is_better=False,
                     fallback=m.get("resting_hr"))
    _headline_metric(c[2], "Weight", mdf, "body_weight_lbs", suffix=" lb", higher_is_better=False)
    _headline_metric(c[3], "Body fat", mdf, "body_fat_pct", suffix=" %", higher_is_better=False)

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**HRV vs 7-day average**")
            _chart(mdf, {"hrv_rmssd": "HRV", "hrv_7d_avg": "7-day avg"})
        with st.container(border=True):
            st.markdown("**Body weight**")
            _chart(mdf, {"body_weight_lbs": "Weight (lb)"})
    with right:
        with st.container(border=True):
            st.markdown("**Resting heart rate**")
            _chart(mdf, {"resting_hr": "Resting HR"})
        with st.container(border=True):
            st.markdown("**Blood oxygen (SpO₂)**")
            _chart(mdf, {"spo2_pct": "SpO₂ %"})


def _tab_training(ctx: dict, fdf, wdf, mode: str) -> None:
    f = ctx.get("features") or {}
    c = st.columns(4)
    c[0].metric("Strength sessions (7d)", _fmt(f.get("strength_sessions_7d")))
    c[1].metric("Cardio minutes (7d)", _fmt(f.get("cardio_minutes_7d")))
    _headline_metric(c[2], "Cardio load (7d)", fdf, "training_load_cardio_minutes_7d")
    _headline_metric(c[3], "Effort index (7d)", fdf, "effort_unified_index_7d")

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Cardio minutes (rolling 7d)**")
            _chart(fdf, {"cardio_minutes_7d": "Cardio min 7d"}, kind="area")
        with st.container(border=True):
            st.markdown("**Strength tonnage (short tons, 7d)**")
            _chart(fdf, {"strength_tonnage_7d": "Tonnage 7d"}, kind="area")
    with right:
        with st.container(border=True):
            st.markdown("**Acute:chronic workload ratio**")
            _chart(fdf, {"acute_chronic_ratio": "ACWR"})
            st.caption("Sweet spot ≈ 0.8–1.3; spikes above ~1.5 raise injury risk.")
        with st.container(border=True):
            st.markdown("**Weekly volume**")
            _chart(
                wdf,
                {"strength_sessions": "Strength", "running_km": "Running km", "cardio_minutes": "Cardio min"},
                kind="bar",
                empty="No weekly summaries yet.",
            )

    if mode == "live":
        from pipeline.dashboard_queries import fetch_cardio_breakdown_7d

        as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
        with _scoped_conn(ctx["user_id"]) as conn:
            breakdown = fetch_cardio_breakdown_7d(conn, user_id=ctx["user_id"], as_of=as_of)
        with st.expander("Cardio breakdown by source (rolling 7d)"):
            if breakdown:
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
            else:
                st.caption("No cardio_events in the rolling 7-day window.")


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
            st.markdown("**Sleep duration vs 7-day average**")
            _chart(mdf, {"sleep_hours": "Sleep (h)", "sleep_7d_avg": "7-day avg"})
        with st.container(border=True):
            st.markdown("**Sleep stages (deep + REM)**")
            _chart(mdf, {"sleep_deep_hrs": "Deep", "sleep_rem_hrs": "REM"}, kind="bar")
    with right:
        with st.container(border=True):
            st.markdown("**Sleep score**")
            _chart(mdf, {"sleep_score": "Sleep score"})
        with st.container(border=True):
            st.markdown("**Sleep debt (rolling 7d)**")
            _chart(fdf, {"sleep_debt_7d": "Sleep debt (h)"}, kind="area")


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


def _page_chat(ctx: dict, mode: str, guidelines: GuidelinesContext | None) -> None:
    st.markdown("#### 💬 Coaching chat")
    st.caption(
        "Ask about today's briefing or your history (e.g. \"how has my sleep trended "
        "over 30 days?\") — trend questions run a read-only, schema-bound query and "
        "get summarized inline."
    )
    saved_msg = st.session_state.pop("_coaching_saved", None)
    if saved_msg:
        st.success(f"Saved: {saved_msg}")

    user_id = ctx["user_id"]
    if "chat_messages" not in st.session_state:
        if mode == "live":
            try:
                with _scoped_conn(user_id) as conn:
                    st.session_state.chat_messages = load_chat_messages(
                        conn, user_id=user_id
                    )
            except Exception:
                st.session_state.chat_messages = []
        else:
            st.session_state.chat_messages = []

    user_input = st.chat_input("Ask Soma…")
    if user_input:
        turn = run_coaching_turn(
            user_id=user_id,
            user_message=user_input,
            dashboard_context=ctx,
            messages=st.session_state.chat_messages,
            llm=_resolve_llm(ctx),
            guidelines=guidelines,
            query_all=_history_query_all(user_id, mode),
        )
        st.session_state["_last_query_results"] = turn.get("query_results") or []
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        st.session_state.chat_messages.append(
            {"role": "assistant", "content": turn["reply"]}
        )
        pending = turn.get("pending_writes") or []
        if pending and mode == "live":
            try:
                applied = _persist_coaching_writes(user_id, pending)
                if applied:
                    _load_live_context.clear()
                    st.session_state["_coaching_saved"] = "; ".join(applied)
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
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to save: {exc}")
        elif pending and turn.get("tool_results") and _debug_enabled():
            with st.expander("🛠 Tool calls (developer — not saved)"):
                st.json(turn["tool_results"])
        elif mode == "live":
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
            except Exception:
                pass

    if not st.session_state.chat_messages:
        st.info("👋 Ask Soma anything about your training, recovery, or trends to get started.")

    for msg in st.session_state.chat_messages:
        avatar = "🧑" if msg["role"] == "user" else "🧬"
        with st.chat_message(msg["role"], avatar=avatar):
            st.write(msg["content"])

    _render_query_details(st.session_state.get("_last_query_results") or [])


def main() -> None:
    _load_dotenv()
    _apply_streamlit_secrets()
    st.set_page_config(page_title="Soma", layout="wide", initial_sidebar_state="expanded")

    cookie_manager = _cookie_manager()
    _restore_session_from_cookie(cookie_manager)

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
    mdf = _history_df(history["metrics"], "metric_date")
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

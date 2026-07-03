"""Phase 9 Streamlit dashboard (Slices A–C + Phase 10 guidelines).

Fixture mode: ``SOMA_DASHBOARD_FIXTURE=1`` (or omit DB env vars).
Live mode: ``SOMA_USER_ID`` + ``SOMA_DATABASE_URL`` (or ``DB_CONNECT_STRING``).
Auth mode: ``SUPABASE_URL`` + ``SUPABASE_ANON_KEY`` for sign-in UI.

Run: ``streamlit run dashboard/app.py`` (requires ``pip install -e '.[dashboard]'``).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from pipeline.coaching_chat import load_chat_messages, run_coaching_turn, save_chat_messages
from pipeline.dashboard_queries import load_dashboard_context_from_db
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


def _render_auth_gate() -> bool:
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
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.info(
        "Operator mode: set SOMA_USER_ID + SOMA_DATABASE_URL without Supabase keys, "
        "or SOMA_DASHBOARD_FIXTURE=1 for demo data."
    )
    st.stop()


def _render_sidebar(mode: str, ctx: dict, guidelines: GuidelinesContext | None) -> None:
    with st.sidebar:
        st.header("Soma")
        st.caption(f"Data: **{mode}**")
        if st.session_state.get("auth_email"):
            st.caption(f"Signed in: {st.session_state.auth_email}")
            if st.button("Sign out"):
                for key in ("auth_user_id", "auth_email", "auth_token", "chat_messages"):
                    st.session_state.pop(key, None)
                st.rerun()
        elif mode == "live":
            uid = ctx.get("user_id", "")
            st.caption(f"User: `{uid[:8]}…`")
        if mode == "live" and st.button("Refresh data"):
            _load_live_context.clear()
            st.rerun()
        if guidelines and guidelines.has_content():
            with st.expander("Personal context (guidelines)"):
                if guidelines.injury_history:
                    st.markdown("**Injury history**")
                    st.caption(guidelines.injury_history[:400])
                if guidelines.my_goals:
                    st.markdown("**Goals**")
                    st.caption(guidelines.my_goals[:400])


def _page_dashboard(ctx: dict, mode: str) -> None:
    from pipeline.dashboard_queries import fetch_cardio_breakdown_7d

    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Today's focus")
        st.info(ctx.get("todays_focus") or "No focus computed yet.")
        if ctx.get("goals_status"):
            st.json(ctx["goals_status"])
    with col2:
        st.subheader("Latest briefing")
        briefing = ctx.get("briefing") or {}
        st.write(briefing.get("coaching_note", "—"))
        if briefing.get("flags"):
            st.caption(f"Flags: {', '.join(briefing['flags'])}")

    features = ctx.get("features") or {}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Strength sessions (7d)", features.get("strength_sessions_7d"))
    m2.metric("Cardio min (7d)", features.get("cardio_minutes_7d"))
    m3.metric("Cardio load 7d", features.get("training_load_cardio_minutes_7d"))
    m4.metric("Readiness", features.get("overall_readiness_score"))

    if mode == "live":
        with _scoped_conn(ctx["user_id"]) as conn:
            breakdown = fetch_cardio_breakdown_7d(conn, user_id=ctx["user_id"], as_of=as_of)
        with st.expander("Cardio breakdown (rolling 7d)"):
            if breakdown:
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
            else:
                st.caption("No cardio_events in the rolling 7-day window.")

    weekly = ctx.get("weekly_summary")
    if weekly:
        tonnage = weekly.get("strength_short_tons")
        tonnage_part = f" · {tonnage} short tons lifted" if tonnage is not None else ""
        st.caption(
            f"Calendar week (Mon {weekly.get('week_start')}): "
            f"{weekly.get('strength_sessions')} strength · "
            f"{weekly.get('running_km')} km · "
            f"{weekly.get('cardio_minutes')} cardio min"
            f"{tonnage_part}"
        )

    sync_rows = ctx.get("sync_health") or []
    if sync_rows:
        st.subheader("Sync health")
        for row in sync_rows:
            st.write(
                f"**{row.get('provider')}**: {row.get('status')} — "
                f"last sync {row.get('last_sync_at') or 'never'}"
            )

    anomalies = ctx.get("recent_anomalies") or []
    if anomalies:
        st.subheader("Recent anomalies")
        for row in anomalies:
            st.write(f"**{row.get('date')}** {row.get('metric')}: {row.get('description')}")


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
    st.subheader("Coaching chat")
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
        elif pending and turn.get("tool_results"):
            with st.expander("Tool calls (fixture — not saved)"):
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

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    _render_query_details(st.session_state.get("_last_query_results") or [])


def main() -> None:
    _load_dotenv()
    st.set_page_config(page_title="Soma", layout="wide", initial_sidebar_state="expanded")

    if not _render_auth_gate():
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
    _render_sidebar(mode, ctx, guidelines)

    page = st.radio(
        "Navigate",
        ["Dashboard", "Coaching chat"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if page == "Dashboard":
        _page_dashboard(ctx, mode)
    else:
        _page_chat(ctx, mode, guidelines)

    with st.expander("Raw dashboard context"):
        st.code(json.dumps(ctx, indent=2, default=str))


if __name__ == "__main__":
    main()

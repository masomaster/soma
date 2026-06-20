"""Phase 9 Streamlit dashboard spike (Slices A–C).

Fixture mode: ``SOMA_DASHBOARD_FIXTURE=1`` (or omit DB env vars).
Live mode: ``SOMA_USER_ID`` + ``SOMA_DATABASE_URL`` (or ``DB_CONNECT_STRING``)
in repo-root ``.env``; set ``SOMA_DASHBOARD_FIXTURE=0`` to force live.

Run: ``streamlit run dashboard/app.py`` (requires ``pip install -e '.[dashboard]'``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from pipeline.coaching_chat import run_coaching_turn
from pipeline.dashboard_queries import (
    build_dashboard_context,
    fetch_cardio_breakdown_7d,
    load_dashboard_context_from_db,
)
from pipeline.goal_tools import apply_coaching_writes

try:
    import streamlit as st
except ImportError as exc:
    raise SystemExit(
        "Streamlit not installed. Run: pip install -e '.[dashboard]'"
    ) from exc

_REPO_ROOT = Path(__file__).resolve().parents[1]


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
    return not (_resolve_db_url() and os.environ.get("SOMA_USER_ID", "").strip())


def _fixture_context() -> dict:
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


@st.cache_data(ttl=60)
def _load_cardio_breakdown(user_id: str, as_of_iso: str) -> list[dict]:
    with _pg_conn() as conn:
        return fetch_cardio_breakdown_7d(
            conn,
            user_id=user_id,
            as_of=date.fromisoformat(as_of_iso),
        )


def _persist_coaching_writes(pending_writes: list[dict]) -> list[str]:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            applied = apply_coaching_writes(cur, pending_writes)
        conn.commit()
        return applied


@st.cache_data(ttl=60)
def _load_live_context(user_id: str, as_of_iso: str) -> dict:
    with _pg_conn() as conn:
        return load_dashboard_context_from_db(
            conn,
            user_id=user_id,
            as_of=date.fromisoformat(as_of_iso),
        )


def _load_context() -> tuple[dict, str]:
    if _fixture_mode_enabled():
        return _fixture_context(), "fixture"

    user_id = os.environ.get("SOMA_USER_ID", "").strip()
    if not user_id or not _resolve_db_url():
        st.error(
            "Live mode requires SOMA_USER_ID and SOMA_DATABASE_URL "
            "(or DB_CONNECT_STRING) in .env. Set SOMA_DASHBOARD_FIXTURE=1 for demo data."
        )
        st.stop()

    as_of = date.today()
    try:
        import psycopg2

        ctx = _load_live_context(user_id, as_of.isoformat())
    except psycopg2.Error as exc:
        st.error(f"Failed to load dashboard from Postgres: {exc}")
        st.stop()
    return ctx, "live"


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
        del system, prompt
        return (
            f"Based on your data: {ctx.get('todays_focus', 'stay consistent')}. "
            "Set ANTHROPIC_API_KEY in .env for real coaching replies."
        )

    return _mock_llm


def main() -> None:
    _load_dotenv()

    st.set_page_config(page_title="Soma", layout="wide")
    st.title("Soma — Health Dashboard")

    ctx, mode = _load_context()
    as_of = date.fromisoformat(str(ctx.get("as_of", date.today().isoformat()))[:10])
    with st.sidebar:
        st.caption(f"Data source: **{mode}**")
        if mode == "live":
            st.caption(f"User: `{ctx.get('user_id', '')[:8]}…`")
            if st.button("Refresh data"):
                _load_live_context.clear()
                _load_cardio_breakdown.clear()
                st.rerun()
        else:
            st.caption("Set SOMA_DASHBOARD_FIXTURE=0 with DB env vars for live rows.")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Today's focus")
        st.info(ctx.get("todays_focus") or "No focus computed yet.")
        if ctx.get("goals_status"):
            st.json(ctx["goals_status"])
        mileage = ctx.get("mileage_check")
        if mileage:
            st.caption(f"Mileage: {mileage}")
    with col2:
        st.subheader("Latest briefing")
        briefing = ctx.get("briefing") or {}
        st.write(briefing.get("coaching_note", "—"))
        if briefing.get("flags"):
            st.caption(f"Flags: {', '.join(briefing['flags'])}")

    st.subheader("Training load (7d / 28d)")
    features = ctx.get("features") or {}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Strength sessions (rolling 7d)", features.get("strength_sessions_7d"))
    m2.metric("Cardio min (rolling 7d)", features.get("cardio_minutes_7d"))
    m3.metric("Cardio load 7d", features.get("training_load_cardio_minutes_7d"))
    m4.metric("Readiness", features.get("overall_readiness_score"))

    if mode == "live":
        breakdown = _load_cardio_breakdown(ctx["user_id"], as_of.isoformat())
        with st.expander("Cardio breakdown (rolling 7d) — check for duplicate sources"):
            if breakdown:
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
                total = sum(float(r.get("minutes") or 0) for r in breakdown)
                st.caption(
                    f"Sum of grouped rows: {total:.1f} min. "
                    "Multiple sources on the same day (e.g. Apple + Hevy strength) "
                    "can inflate totals — dedup runs at ingest, not retroactively."
                )
            else:
                st.caption("No cardio_events in the rolling 7-day window.")

    metrics = ctx.get("today_metrics") or {}
    if metrics:
        st.caption(
            f"Latest metrics ({metrics.get('date', '—')}): "
            f"HRV {metrics.get('hrv_rmssd', '—')} · "
            f"sleep {metrics.get('sleep_hours', '—')}h · "
            f"RHR {metrics.get('resting_hr', '—')}"
        )

    weekly = ctx.get("weekly_summary")
    if weekly:
        st.caption(
            f"Calendar week (Mon {weekly.get('week_start')}): "
            f"{weekly.get('strength_sessions')} strength · "
            f"{weekly.get('running_km')} km run · "
            f"{weekly.get('cardio_minutes')} cardio min"
        )

    st.subheader("Sync health")
    sync_rows = ctx.get("sync_health") or []
    if sync_rows:
        for row in sync_rows:
            st.write(
                f"**{row.get('provider')}**: {row.get('status')} — "
                f"last sync {row.get('last_sync_at') or 'never'}"
            )
    else:
        st.caption("No provider_connections rows yet.")

    anomalies = ctx.get("recent_anomalies") or []
    if anomalies:
        st.subheader("Recent anomalies")
        for row in anomalies:
            st.write(f"**{row.get('date')}** {row.get('metric')}: {row.get('description')}")

    st.subheader("Coaching chat")
    saved_msg = st.session_state.pop("_coaching_saved", None)
    if saved_msg:
        st.success(f"Saved: {saved_msg}")
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    user_input = st.chat_input("Ask Soma…")
    if user_input:
        turn = run_coaching_turn(
            user_id=ctx["user_id"],
            user_message=user_input,
            dashboard_context=ctx,
            messages=st.session_state.chat_messages,
            llm=_resolve_llm(ctx),
        )
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        st.session_state.chat_messages.append({"role": "assistant", "content": turn["reply"]})
        pending = turn.get("pending_writes") or []
        if pending and mode == "live":
            try:
                import psycopg2

                applied = _persist_coaching_writes(pending)
                if applied:
                    _load_live_context.clear()
                    _load_cardio_breakdown.clear()
                    st.session_state["_coaching_saved"] = "; ".join(applied)
                    st.rerun()
            except psycopg2.Error as exc:
                st.error(f"Failed to save changes: {exc}")
        elif pending and turn.get("tool_results"):
            with st.expander("Tool calls (fixture mode — not saved)"):
                st.json(turn["tool_results"])

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    with st.expander("Raw dashboard context"):
        st.code(json.dumps(ctx, indent=2, default=str))


if __name__ == "__main__":
    main()

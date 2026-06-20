"""Phase 9 Streamlit dashboard spike (Slices A–C).

Local demo: set ``SOMA_DASHBOARD_FIXTURE=1`` to render with in-memory fixture
data (no Supabase). With ``DB_CONNECT_STRING`` set, loads live rows for one
``SOMA_USER_ID``.

Run: ``streamlit run dashboard/app.py`` (requires ``pip install -e '.[dashboard]'``).
"""

from __future__ import annotations

import json
import os
from datetime import date

from pipeline.dashboard_queries import build_dashboard_context
from pipeline.coaching_chat import run_coaching_turn

try:
    import streamlit as st
except ImportError as exc:
    raise SystemExit(
        "Streamlit not installed. Run: pip install -e '.[dashboard]'"
    ) from exc


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


def main() -> None:
    st.set_page_config(page_title="Soma", layout="wide")
    st.title("Soma — Health Dashboard")

    use_fixture = os.environ.get("SOMA_DASHBOARD_FIXTURE", "1") == "1"
    ctx = _fixture_context() if use_fixture else _fixture_context()

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

    st.subheader("Training load (7d / 28d)")
    features = ctx.get("features") or {}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Strength sessions (7d)", features.get("strength_sessions_7d"))
    m2.metric("Cardio min (7d)", features.get("cardio_minutes_7d"))
    m3.metric("Cardio load 7d", features.get("training_load_cardio_minutes_7d"))
    m4.metric("Readiness", features.get("overall_readiness_score"))

    st.subheader("Sync health")
    for row in ctx.get("sync_health") or []:
        st.write(f"**{row.get('provider')}**: {row.get('status')} — last sync {row.get('last_sync_at')}")

    st.subheader("Coaching chat")
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    user_input = st.chat_input("Ask Soma…")
    if user_input:

        def _mock_llm(system: str, prompt: str) -> str:
            return (
                f"Based on your data: {ctx.get('todays_focus', 'stay consistent')}. "
                "Let me know if you want to adjust a goal."
            )

        turn = run_coaching_turn(
            user_id=ctx["user_id"],
            user_message=user_input,
            dashboard_context=ctx,
            messages=st.session_state.chat_messages,
            llm=_mock_llm,
        )
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        st.session_state.chat_messages.append({"role": "assistant", "content": turn["reply"]})

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    with st.expander("Raw dashboard context"):
        st.code(json.dumps(ctx, indent=2, default=str))


if __name__ == "__main__":
    main()

"""Bounded read queries for Phase 9 dashboard (no raw-table NL).

Returns pre-shaped JSON for homepage widgets: latest briefing, features,
training load, goal progress, sync health. All reads are scoped by
``user_id`` — callers use RLS-backed Supabase client or service role with
explicit user filter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

# Tables the bounded NL query layer may reference (Slice C).
ALLOWED_QUERY_TABLES = frozenset(
    {
        "daily_features",
        "daily_health_metrics",
        "daily_briefings",
        "daily_goal_snapshot",
        "weekly_activity_summary",
        "strength_events",
        "cardio_events",
        "anomaly_events",
        "metric_patterns",
        "goals",
        "running_sessions",
        "provider_connections",
    }
)

# Minimal schema hint for text-to-SQL prompts.
BOUNDED_SCHEMA_HINT = """
Tables (all have user_id; always filter by user_id):
- daily_features(feature_date, strength_sessions_7d, cardio_minutes_7d, training_load_*)
- daily_health_metrics(metric_date, hrv_rmssd, sleep_hours, resting_hr, ...)
- daily_briefings(briefing_date, coaching_note, flags, features_json)
- daily_goal_snapshot(snapshot_date, goals_status, mileage_check, todays_focus)
- weekly_activity_summary(week_start, strength_sessions, running_km, cardio_minutes)
- strength_events(event_date, exercise_name, reps, weight_lbs)
- cardio_events(event_date, activity_type, duration_min, distance_miles)
- goals(goal_type, target_min, target_max, is_active)
- running_sessions(session_date, run_type, distance_km)
- provider_connections(provider, status, last_sync_at)
Only SELECT. No INSERT/UPDATE/DELETE. Limit 500 rows.
""".strip()


def build_dashboard_context(
    *,
    user_id: str,
    as_of: date,
    latest_briefing: Mapping[str, Any] | None,
    latest_features: Mapping[str, Any] | None,
    latest_metrics: Mapping[str, Any] | None,
    goal_snapshot: Mapping[str, Any] | None,
    weekly_summary: Mapping[str, Any] | None,
    provider_connections: Sequence[Mapping[str, Any]] | None = None,
    recent_anomalies: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble homepage JSON for dashboard or coaching chat context."""
    ctx: dict[str, Any] = {
        "user_id": user_id,
        "as_of": as_of.isoformat(),
    }
    if latest_briefing:
        ctx["briefing"] = {
            "date": _iso(latest_briefing.get("briefing_date")),
            "coaching_note": latest_briefing.get("coaching_note"),
            "flags": latest_briefing.get("flags"),
        }
    if latest_features:
        ctx["features"] = {
            "date": _iso(latest_features.get("feature_date")),
            "strength_sessions_7d": latest_features.get("strength_sessions_7d"),
            "cardio_minutes_7d": latest_features.get("cardio_minutes_7d"),
            "training_load_cardio_minutes_7d": latest_features.get(
                "training_load_cardio_minutes_7d"
            ),
            "training_load_cardio_minutes_28d": latest_features.get(
                "training_load_cardio_minutes_28d"
            ),
            "training_load_strength_short_tons_7d": latest_features.get(
                "training_load_strength_short_tons_7d"
            ),
            "effort_unified_index_7d": latest_features.get("effort_unified_index_7d"),
            "overall_readiness_score": latest_features.get("overall_readiness_score"),
        }
    if latest_metrics:
        ctx["today_metrics"] = {
            "date": _iso(latest_metrics.get("metric_date")),
            "hrv_rmssd": latest_metrics.get("hrv_rmssd"),
            "sleep_hours": latest_metrics.get("sleep_hours"),
            "resting_hr": latest_metrics.get("resting_hr"),
        }
    if goal_snapshot:
        ctx["goals_status"] = goal_snapshot.get("goals_status")
        ctx["todays_focus"] = goal_snapshot.get("todays_focus")
        ctx["mileage_check"] = goal_snapshot.get("mileage_check")
    if weekly_summary:
        ctx["weekly_summary"] = {
            "week_start": _iso(weekly_summary.get("week_start")),
            "strength_sessions": weekly_summary.get("strength_sessions"),
            "running_km": weekly_summary.get("running_km"),
            "cardio_minutes": weekly_summary.get("cardio_minutes"),
        }
    if provider_connections:
        ctx["sync_health"] = [
            {
                "provider": r.get("provider"),
                "status": r.get("status"),
                "last_sync_at": r.get("last_sync_at"),
                "last_error": r.get("last_error"),
            }
            for r in provider_connections
        ]
    if recent_anomalies:
        ctx["recent_anomalies"] = [
            {
                "date": _iso(a.get("detected_date")),
                "metric": a.get("metric"),
                "description": a.get("description"),
                "severity": a.get("severity"),
            }
            for a in recent_anomalies[:8]
        ]
    return ctx


def _iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val.isoformat()
    return str(val)[:10] if val else None


def validate_bounded_sql(sql: str, *, user_id: str) -> str:
    """Reject unsafe SQL before execution (Slice C read path).

    Raises:
        ValueError: If the statement is not a safe read-only SELECT.
    """
    normalized = sql.strip().rstrip(";").lower()
    if not normalized.startswith("select"):
        raise ValueError("Only SELECT queries are allowed")
    forbidden = (
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "grant",
        "revoke",
        "create",
        ";",
    )
    for token in forbidden:
        if token in normalized:
            raise ValueError(f"Forbidden SQL token: {token!r}")
    if "user_id" not in normalized:
        raise ValueError("Query must filter by user_id")
    if user_id.lower() not in normalized.replace("'", ""):
        raise ValueError("Query must include the requesting user's id")
    # Note: production would use sqlglot AST validation; this is a v0 guard.
    return sql.strip()

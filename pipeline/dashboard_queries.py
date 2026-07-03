"""Bounded read queries for Phase 9 dashboard (no raw-table NL).

Returns pre-shaped JSON for homepage widgets: latest briefing, features,
training load, goal progress, sync health. All reads are scoped by
``user_id`` — callers use RLS-backed Supabase client or service role with
explicit user filter.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pipeline.features import ACUTE_WINDOW_DAYS


def _utc_today() -> date:
    """Today's calendar date in UTC (consistent with the rest of the pipeline)."""
    return datetime.now(timezone.utc).date()

QueryOne = Callable[[str, tuple[Any, ...]], Mapping[str, Any] | None]
QueryAll = Callable[[str, tuple[Any, ...]], Sequence[Mapping[str, Any]]]

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

# Maximum rows a bounded NL query may return (enforced by validate_bounded_sql).
MAX_QUERY_ROWS = 500

# Upper bound on how far back the dashboard trend charts may look (defense in
# depth alongside the LIMIT so a caller cannot request an unbounded scan).
MAX_HISTORY_DAYS = 370


def _coerce_json_mapping(value: Any) -> dict[str, Any]:
    """Normalize JSONB values that may arrive as dict or serialized string."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# Minimal schema hint for text-to-SQL prompts.
BOUNDED_SCHEMA_HINT = f"""
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
Only SELECT. No INSERT/UPDATE/DELETE. Limit {MAX_QUERY_ROWS} rows.
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
        ctx["goals_status"] = _coerce_json_mapping(goal_snapshot.get("goals_status")) or None
        ctx["todays_focus"] = goal_snapshot.get("todays_focus")
        mileage = _coerce_json_mapping(goal_snapshot.get("mileage_check"))
        ctx["mileage_check"] = mileage or goal_snapshot.get("mileage_check")
    if weekly_summary:
        summary_json = _coerce_json_mapping(weekly_summary.get("summary_json"))
        ctx["weekly_summary"] = {
            "week_start": _iso(weekly_summary.get("week_start")),
            "strength_sessions": weekly_summary.get("strength_sessions"),
            "running_km": weekly_summary.get("running_km"),
            "cardio_minutes": weekly_summary.get("cardio_minutes"),
            "strength_short_tons": summary_json.get("strength_short_tons"),
            "strength_hard_sets": summary_json.get("strength_hard_sets"),
            "strength_volume_lbs": summary_json.get("strength_volume_lbs"),
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
            for a in recent_anomalies
        ]
    return ctx


def fetch_dashboard_source_rows(
    *,
    user_id: str,
    as_of: date,
    query_one: QueryOne,
    query_all: QueryAll,
) -> dict[str, Any]:
    """Load latest dashboard rows via injected read-only queries."""
    latest_briefing = query_one(
        "SELECT briefing_date, coaching_note, flags FROM daily_briefings "
        "WHERE user_id = %s AND briefing_date <= %s "
        "ORDER BY briefing_date DESC LIMIT 1",
        (user_id, as_of),
    )
    latest_features = query_one(
        "SELECT feature_date, strength_sessions_7d, cardio_minutes_7d, "
        "training_load_cardio_minutes_7d, training_load_cardio_minutes_28d, "
        "training_load_strength_short_tons_7d, effort_unified_index_7d, "
        "overall_readiness_score FROM daily_features "
        "WHERE user_id = %s AND feature_date <= %s "
        "ORDER BY feature_date DESC LIMIT 1",
        (user_id, as_of),
    )
    latest_metrics = query_one(
        "SELECT metric_date, hrv_rmssd, sleep_hours, resting_hr "
        "FROM daily_health_metrics "
        "WHERE user_id = %s AND metric_date <= %s "
        "ORDER BY metric_date DESC LIMIT 1",
        (user_id, as_of),
    )
    snapshot_row = query_one(
        "SELECT snapshot_date, goals_status, mileage_check, todays_focus "
        "FROM daily_goal_snapshot "
        "WHERE user_id = %s AND snapshot_date <= %s "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (user_id, as_of),
    )
    weekly_summary = query_one(
        "SELECT week_start, strength_sessions, running_km, cardio_minutes, summary_json "
        "FROM weekly_activity_summary "
        "WHERE user_id = %s AND week_start <= %s "
        "ORDER BY week_start DESC LIMIT 1",
        (user_id, as_of),
    )
    provider_connections = list(
        query_all(
            "SELECT provider, status, last_sync_at, last_error "
            "FROM provider_connections WHERE user_id = %s ORDER BY provider",
            (user_id,),
        )
    )
    recent_anomalies = list(
        query_all(
            "SELECT detected_date, metric, description, severity "
            "FROM anomaly_events "
            "WHERE user_id = %s AND detected_date <= %s "
            "ORDER BY detected_date DESC LIMIT 8",
            (user_id, as_of),
        )
    )
    return build_dashboard_context(
        user_id=user_id,
        as_of=as_of,
        latest_briefing=latest_briefing,
        latest_features=latest_features,
        latest_metrics=latest_metrics,
        goal_snapshot=snapshot_row,
        weekly_summary=weekly_summary,
        provider_connections=provider_connections,
        recent_anomalies=recent_anomalies,
    )


def load_dashboard_context_from_db(
    conn: Any,
    *,
    user_id: str,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Load dashboard context from Postgres using a psycopg2 connection.

    For end-user surfaces the caller must first bind the transaction to the
    requesting user with :func:`pipeline.db_session.apply_rls_scope` so RLS
    enforces isolation; the ``user_id`` predicates below are defense in depth.
    """
    from psycopg2.extras import RealDictCursor

    effective = as_of or _utc_today()

    def query_one(sql: str, params: tuple[Any, ...]) -> Mapping[str, Any] | None:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def query_all(sql: str, params: tuple[Any, ...]) -> Sequence[Mapping[str, Any]]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    return fetch_dashboard_source_rows(
        user_id=user_id,
        as_of=effective,
        query_one=query_one,
        query_all=query_all,
    )


def fetch_cardio_breakdown_7d(
    conn: Any,
    *,
    user_id: str,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Per-day cardio totals by source + source_app + activity (rolling 7d).

    ``source_app`` surfaces the originating HealthKit app (Nike Run Club / Strava /
    Health Sync) so surviving duplicates are attributable. Matches the features window.
    """
    from psycopg2.extras import RealDictCursor

    effective = as_of or _utc_today()
    window_start = effective - timedelta(days=ACUTE_WINDOW_DAYS - 1)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT event_date, source, "
            "COALESCE(source_app, '(unknown)') AS source_app, activity_type, "
            "COUNT(*) AS row_count, ROUND(SUM(duration_min)::numeric, 1) AS minutes "
            "FROM cardio_events "
            "WHERE user_id = %s AND event_date BETWEEN %s AND %s "
            "GROUP BY event_date, source, source_app, activity_type "
            "ORDER BY event_date DESC, minutes DESC",
            (user_id, window_start, effective),
        )
        return [dict(r) for r in cur.fetchall()]


def _clamp_days(days: int) -> int:
    """Clamp a requested trend window to ``[1, MAX_HISTORY_DAYS]``."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        return MAX_HISTORY_DAYS
    return max(1, min(value, MAX_HISTORY_DAYS))


# Column projections for the trend charts. Kept as tuples so the SELECT list is
# an explicit allow-list (no SELECT *), mirroring the other bounded reads above.
_METRICS_HISTORY_COLUMNS = (
    "metric_date",
    "hrv_rmssd",
    "resting_hr",
    "spo2_pct",
    "sleep_hours",
    "sleep_deep_hrs",
    "sleep_rem_hrs",
    "sleep_score",
    "steps",
    "active_cal",
    "body_weight_lbs",
    "body_fat_pct",
    "hrv_7d_avg",
    "sleep_7d_avg",
)

_FEATURES_HISTORY_COLUMNS = (
    "feature_date",
    "overall_readiness_score",
    "acute_chronic_ratio",
    "cardio_minutes_7d",
    "training_load_cardio_minutes_7d",
    "training_load_cardio_minutes_28d",
    "strength_tonnage_7d",
    "strength_sessions_7d",
    "effort_unified_index_7d",
    "sleep_debt_7d",
)

_WEEKLY_HISTORY_COLUMNS = (
    "week_start",
    "strength_sessions",
    "running_km",
    "cardio_minutes",
)


def _fetch_history(
    conn: Any,
    *,
    table: str,
    date_column: str,
    columns: Sequence[str],
    user_id: str,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """Run one bounded, user-scoped, ascending time-series read.

    All charts flow through here so the safety properties are uniform: an
    explicit column allow-list (no ``SELECT *``), a parameterized ``user_id``
    predicate (defense in depth in front of RLS, which the caller must have
    bound via :func:`pipeline.db_session.apply_rls_scope`), a bounded date
    window, and a hard ``LIMIT``. ``table`` / ``date_column`` / ``columns`` are
    module-internal literals only — never caller-controlled — so the f-string
    below interpolates trusted identifiers, not user input.
    """
    from psycopg2.extras import RealDictCursor

    projection = ", ".join(columns)
    sql = (
        f"SELECT {projection} FROM {table} "
        f"WHERE user_id = %s AND {date_column} BETWEEN %s AND %s "
        f"ORDER BY {date_column} ASC LIMIT {MAX_QUERY_ROWS}"
    )
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (user_id, window_start, window_end))
        return [dict(r) for r in cur.fetchall()]


def fetch_metrics_history(
    conn: Any,
    *,
    user_id: str,
    as_of: date | None = None,
    days: int = 90,
) -> list[dict[str, Any]]:
    """Daily biometric time series (sleep / HRV / resting HR / weight / steps)."""
    effective = as_of or _utc_today()
    window = _clamp_days(days)
    return _fetch_history(
        conn,
        table="daily_health_metrics",
        date_column="metric_date",
        columns=_METRICS_HISTORY_COLUMNS,
        user_id=user_id,
        window_start=effective - timedelta(days=window - 1),
        window_end=effective,
    )


def fetch_features_history(
    conn: Any,
    *,
    user_id: str,
    as_of: date | None = None,
    days: int = 90,
) -> list[dict[str, Any]]:
    """Daily feature time series (readiness / ACWR / training load / effort)."""
    effective = as_of or _utc_today()
    window = _clamp_days(days)
    return _fetch_history(
        conn,
        table="daily_features",
        date_column="feature_date",
        columns=_FEATURES_HISTORY_COLUMNS,
        user_id=user_id,
        window_start=effective - timedelta(days=window - 1),
        window_end=effective,
    )


def fetch_weekly_summaries(
    conn: Any,
    *,
    user_id: str,
    as_of: date | None = None,
    weeks: int = 12,
) -> list[dict[str, Any]]:
    """Weekly activity rollups (strength sessions / running km / cardio min)."""
    effective = as_of or _utc_today()
    window = _clamp_days(weeks * 7)
    return _fetch_history(
        conn,
        table="weekly_activity_summary",
        date_column="week_start",
        columns=_WEEKLY_HISTORY_COLUMNS,
        user_id=user_id,
        window_start=effective - timedelta(days=window),
        window_end=effective,
    )


def _iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val.isoformat()
    return str(val)[:10] if val else None


# Functions that can read files, sleep, exfiltrate, or reach outside the row set.
# Blocked even inside an otherwise-valid SELECT (defense in depth alongside RLS).
FORBIDDEN_SQL_FUNCTIONS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_read_server_files",
        "lo_import",
        "lo_export",
        "lo_get",
        "dblink",
        "dblink_exec",
        "copy",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "set_config",
        "current_setting",
        "query_to_xml",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "txid_current",
        "has_table_privilege",
    }
)


def validate_bounded_sql(sql: str, *, user_id: str) -> str:
    """Reject unsafe SQL before execution and return a normalized, bounded SELECT.

    This is the application-layer guard for the Slice C text-to-SQL read path;
    it is defense in depth *in front of* RLS (see :mod:`pipeline.db_session`),
    not a replacement for it. The statement must be a single ``SELECT`` over one
    allow-listed table, filtered by the requesting user's ``user_id`` in the
    ``WHERE`` clause, with no set operations, joins, subqueries, CTEs, ``OR``
    branches, ``INTO``, locking, or file/side-effecting functions. The returned
    SQL is re-rendered from the parsed AST (eliminating any parser differential
    with Postgres) and capped at ``MAX_QUERY_ROWS``.

    Raises:
        ValueError: If the statement is not a safe, single-user, read-only SELECT
            (including when ``sqlglot`` is unavailable — this fails closed).
    """
    cleaned = sql.strip().rstrip(";")
    if not cleaned:
        raise ValueError("Empty SQL")
    try:
        import sqlglot  # noqa: F401
    except ImportError as exc:
        raise ValueError("SQL validation requires sqlglot") from exc
    return _validate_bounded_sql_ast(cleaned, user_id=user_id)


def _validate_bounded_sql_ast(sql: str, *, user_id: str) -> str:
    import sqlglot
    from sqlglot import exp

    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except Exception as exc:
        raise ValueError(f"Invalid SQL: {exc}") from exc

    if len(statements) != 1:
        raise ValueError("Exactly one statement is allowed")
    parsed = statements[0]
    # Top-level set operations parse to a non-Select root; check explicitly so the
    # error is specific (nested ones are caught by the subquery/CTE rejection below).
    if isinstance(parsed, (exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Set operations (UNION/INTERSECT/EXCEPT) are not allowed")
    if not isinstance(parsed, exp.Select):
        raise ValueError("Only SELECT queries are allowed")

    # Reject write/DDL and opaque commands anywhere in the tree.
    for node in parsed.walk():
        if isinstance(
            node,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Drop,
                exp.Alter,
                exp.Create,
                exp.TruncateTable,
                exp.Grant,
                exp.Revoke,
                exp.Command,
            ),
        ):
            raise ValueError(f"Forbidden statement type: {type(node).__name__}")

    if parsed.find(exp.With):
        raise ValueError("CTEs (WITH) are not allowed")
    if parsed.find(exp.Join):
        raise ValueError("JOINs are not allowed")
    # OR can widen a result past the user_id predicate (e.g. `user_id = x OR 1=1`).
    if parsed.find(exp.Or):
        raise ValueError("OR conditions are not allowed")
    # Subqueries / derived tables could reference other users' rows.
    if any(node is not parsed for node in parsed.find_all(exp.Select)):
        raise ValueError("Subqueries are not allowed")
    if parsed.args.get("into") or parsed.find(exp.Into):
        raise ValueError("SELECT INTO is not allowed")
    if parsed.args.get("locks"):
        raise ValueError("Locking clauses (FOR UPDATE/SHARE) are not allowed")

    for name in _function_names(parsed):
        if name in FORBIDDEN_SQL_FUNCTIONS:
            raise ValueError(f"Function not allowed: {name}")

    tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name}
    if len(tables) != 1:
        raise ValueError("Exactly one table may be queried")
    unknown = tables - {t.lower() for t in ALLOWED_QUERY_TABLES}
    if unknown:
        raise ValueError(f"Table not allowed: {', '.join(sorted(unknown))}")

    if not _where_references_user_id(parsed, user_id=user_id):
        raise ValueError("Query must filter by user_id for the requesting user")

    limit_val = _limit_value(parsed)
    if limit_val is None or limit_val > MAX_QUERY_ROWS:
        parsed = parsed.limit(MAX_QUERY_ROWS)

    return parsed.sql(dialect="postgres")


def _function_names(parsed: Any) -> Iterator[str]:
    """Yield normalized names of every function-like node (not just unknown ones)."""
    from sqlglot import exp

    for fn in parsed.find_all(exp.Func):
        name = str(fn.this) if isinstance(fn, exp.Anonymous) else fn.sql_name()
        yield (name or "").lower()


def _limit_value(parsed: Any) -> int | None:
    limit = parsed.args.get("limit")
    if limit is None:
        return None
    try:
        return int(limit.expression.this)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None


def _where_references_user_id(parsed: Any, *, user_id: str) -> bool:
    """True when the WHERE clause pins ``user_id`` to the requesting user.

    Only the WHERE subtree is inspected (not projections), and OR branches are
    already rejected upstream, so a matching equality here is a dominant filter.
    """
    from sqlglot import exp

    where = parsed.args.get("where")
    if where is None:
        return False
    uid = user_id.lower()
    for node in where.walk():
        if not isinstance(node, exp.EQ):
            continue
        for col, other in ((node.left, node.right), (node.right, node.left)):
            if (
                isinstance(col, exp.Column)
                and col.name
                and col.name.lower() == "user_id"
            ):
                literal = _literal_text(other)
                if literal and literal.lower() == uid:
                    return True
    return False


def _literal_text(node: Any) -> str | None:
    from sqlglot import exp

    if isinstance(node, exp.Literal):
        return str(node.this)
    return None

"""Upserts for the Phase 6 analysis tables (``daily_health_metrics``,
``daily_features``, ``daily_briefings``) plus **append-style** writes for
``anomaly_events`` statistical rows (Phase 8).

These tables are **recomputed** each run, so the conflict action is
``DO UPDATE`` (idempotent overwrite) rather than the ``DO NOTHING`` used for
append-only event ingestion. Rows are **sparse by design** — only the columns
present in the dict are written, so columns populated by a different job (e.g.
``daily_health_metrics.hrv_7d_avg``) are preserved rather than nulled. Because a
single computation pass produces the full set of columns it owns, this does not
leave stale values within that pass. Every column name is validated against an
allow-list, so identifiers are never taken from untrusted input. Expects a
psycopg2 cursor on a ``service_role`` connection (RLS bypassed; caller supplies
``user_id``).

``replace_statistical_anomaly_events`` deletes prior **statistical** rows for the
user/day (idempotent pipeline retries) then inserts the new set.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from psycopg2 import sql

logger = logging.getLogger(__name__)

# Per-table: allowed columns and the conflict key. Anything not listed is rejected.
_TABLES: dict[str, dict[str, Any]] = {
    "daily_health_metrics": {
        "conflict": ("user_id", "metric_date"),
        "columns": frozenset(
            {
                "user_id",
                "metric_date",
                "hrv_rmssd",
                "resting_hr",
                "spo2_pct",
                "respiratory_rate",
                "sleep_hours",
                "sleep_deep_hrs",
                "sleep_rem_hrs",
                "sleep_score",
                "steps",
                "active_cal",
                "vo2_max",
                "body_weight_lbs",
                "body_fat_pct",
                "muscle_mass_lbs",
                "hrv_7d_avg",
                "hrv_30d_avg",
                "hrv_baseline_ratio",
                "sleep_7d_avg",
                "weight_30d_trend",
            }
        ),
    },
    "daily_features": {
        "conflict": ("user_id", "feature_date"),
        "columns": frozenset(
            {
                "user_id",
                "feature_date",
                "cardio_sessions_7d",
                "cardio_minutes_7d",
                "cardio_minutes_14d",
                "cardio_trimp_7d",
                "cardio_distance_suspect_7d",
                "acute_chronic_ratio",
                "strength_sessions_7d",
                "strength_hard_sets_7d",
                "strength_tonnage_7d",
                "recovery_sleep_days_7d",
                "recovery_hrv_days_7d",
                "upper_body_sets_7d",
                "lower_body_sets_7d",
                "push_sets_7d",
                "pull_sets_7d",
                "sleep_debt_7d",
                "hrv_suppressed_days",
                "overall_readiness_score",
                "training_load_cardio_minutes_7d",
                "training_load_cardio_minutes_28d",
                "training_load_strength_short_tons_7d",
                "training_load_strength_short_tons_28d",
                "training_load_strength_hard_sets_28d",
                "training_load_strength_sessions_28d",
                "effort_unified_index_7d",
                "effort_unified_index_28d",
                "effort_foster_cardio_au_7d",
                "effort_foster_strength_au_7d",
                "effort_foster_au_7d",
                "effort_foster_cardio_au_28d",
                "effort_foster_strength_au_28d",
                "effort_foster_au_28d",
            }
        ),
    },
    "daily_briefings": {
        "conflict": ("user_id", "briefing_date"),
        "columns": frozenset(
            {
                "user_id",
                "briefing_date",
                "flags",
                "recommendations",
                "features_json",
                "anomalies",
                "coaching_note",
                "model_used",
            }
        ),
    },
    "daily_goal_snapshot": {
        "conflict": ("user_id", "snapshot_date"),
        "columns": frozenset(
            {
                "user_id",
                "snapshot_date",
                "goals_status",
                "mileage_check",
                "todays_focus",
            }
        ),
    },
    "weekly_activity_summary": {
        "conflict": ("user_id", "week_start"),
        "columns": frozenset(
            {
                "user_id",
                "week_start",
                "strength_sessions",
                "running_km",
                "cardio_minutes",
                "summary_json",
            }
        ),
    },
    "goals": {
        "conflict": ("user_id", "goal_type"),
        "columns": frozenset(
            {
                "user_id",
                "goal_type",
                "target_min",
                "target_max",
                "target_label",
                "period",
                "is_active",
                "effective_from",
                "effective_until",
                "notes",
            }
        ),
    },
}

# JSONB columns are serialized before binding so callers can pass plain dicts.
_JSONB_COLUMNS = frozenset(
    {
        "recommendations",
        "features_json",
        "anomalies",
        "goals_status",
        "mileage_check",
        "summary_json",
    }
)


def _prepare(table: str, row: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
    spec = _TABLES[table]
    allowed: frozenset[str] = spec["columns"]
    unknown = [k for k in row if k not in allowed]
    if unknown:
        raise KeyError(f"{table} row has unknown column(s): {sorted(unknown)}")
    for key in spec["conflict"]:
        if row.get(key) is None:
            raise KeyError(f"{table} row missing conflict key {key!r}")
    cols = [c for c in row]
    values = [
        json.dumps(row[c]) if c in _JSONB_COLUMNS and row[c] is not None else row[c] for c in cols
    ]
    return cols, values


def upsert_row(cur: Any, table: str, row: Mapping[str, Any]) -> None:
    """Insert/overwrite one sparse row into an allow-listed analysis table.

    Raises:
        KeyError: Unknown table, unknown column, or missing conflict key.
    """
    if table not in _TABLES:
        raise KeyError(f"Unsupported table for upsert: {table!r}")
    conflict: tuple[str, ...] = _TABLES[table]["conflict"]
    cols, values = _prepare(table, row)

    update_cols = [c for c in cols if c not in conflict]
    assignments = [
        sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in update_cols
    ]
    # Bump the freshness column when the table has one and it wasn't supplied.
    if "updated_at" in _TABLES[table]["columns"] or table in {
        "daily_health_metrics",
        "daily_features",
    }:
        assignments.append(sql.SQL("updated_at = NOW()"))

    if update_cols or assignments:
        conflict_action = sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(assignments)
    else:
        conflict_action = sql.SQL("DO NOTHING")

    statement = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({ph}) ON CONFLICT ({conflict}) {action}"
    ).format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        ph=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        conflict=sql.SQL(", ").join(sql.Identifier(c) for c in conflict),
        action=conflict_action,
    )
    cur.execute(statement, values)
    logger.debug("Upserted row into %s (%d cols)", table, len(cols))


_STATISTICAL_ANOMALY_COLUMNS: frozenset[str] = frozenset(
    {
        "user_id",
        "detected_date",
        "metric",
        "anomaly_type",
        "description",
        "severity",
        "context_json",
    }
)


def replace_statistical_anomaly_events(
    cur: Any,
    *,
    user_id: str,
    detected_date: date,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Remove statistical anomalies for ``(user_id, detected_date)``, then insert ``rows``.

    Uses ``DELETE`` + ``INSERT`` so pipeline retries do not duplicate rows. Only
    ``anomaly_type = 'statistical'`` rows for that day are removed; LLM or other
    anomaly types are left untouched.

    Raises:
        KeyError: Unknown column, missing required field, or wrong ``anomaly_type``.
    """
    for row in rows:
        unknown = set(row) - _STATISTICAL_ANOMALY_COLUMNS
        if unknown:
            raise KeyError(f"anomaly_events row has unknown column(s): {sorted(unknown)}")
        for key in ("user_id", "detected_date", "anomaly_type", "description"):
            if row.get(key) is None:
                raise KeyError(f"anomaly_events row missing required field {key!r}")
        if row.get("anomaly_type") != "statistical":
            raise KeyError("replace_statistical_anomaly_events only accepts anomaly_type 'statistical'")

    cur.execute(
        "DELETE FROM anomaly_events WHERE user_id = %s AND detected_date = %s AND anomaly_type = %s",
        (user_id, detected_date, "statistical"),
    )
    insert_sql = (
        "INSERT INTO anomaly_events "
        "(user_id, detected_date, metric, anomaly_type, description, severity, context_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    for row in rows:
        ctx = row.get("context_json")
        ctx_bound: str | None = json.dumps(ctx) if ctx is not None else None
        cur.execute(
            insert_sql,
            (
                row["user_id"],
                row["detected_date"],
                row.get("metric"),
                row["anomaly_type"],
                row["description"],
                row.get("severity"),
                ctx_bound,
            ),
        )
    logger.debug(
        "Replaced statistical anomaly_events for user %s on %s (%d row(s))",
        user_id,
        detected_date,
        len(rows),
    )


def replace_llm_pattern_anomaly_events(
    cur: Any,
    *,
    user_id: str,
    detected_date: date,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Remove prior ``llm_pattern`` rows for ``(user_id, detected_date)``, then insert ``rows``."""
    for row in rows:
        unknown = set(row) - _STATISTICAL_ANOMALY_COLUMNS
        if unknown:
            raise KeyError(f"anomaly_events row has unknown column(s): {sorted(unknown)}")
        for key in ("user_id", "detected_date", "anomaly_type", "description"):
            if row.get(key) is None:
                raise KeyError(f"anomaly_events row missing required field {key!r}")
        if row.get("anomaly_type") != "llm_pattern":
            raise KeyError("replace_llm_pattern_anomaly_events only accepts anomaly_type 'llm_pattern'")

    cur.execute(
        "DELETE FROM anomaly_events WHERE user_id = %s AND detected_date = %s AND anomaly_type = %s",
        (user_id, detected_date, "llm_pattern"),
    )
    insert_sql = (
        "INSERT INTO anomaly_events "
        "(user_id, detected_date, metric, anomaly_type, description, severity, context_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    for row in rows:
        ctx = row.get("context_json")
        ctx_bound: str | None = json.dumps(ctx) if ctx is not None else None
        cur.execute(
            insert_sql,
            (
                row["user_id"],
                row["detected_date"],
                row.get("metric"),
                row["anomaly_type"],
                row["description"],
                row.get("severity"),
                ctx_bound,
            ),
        )
    logger.debug(
        "Replaced llm_pattern anomaly_events for user %s on %s (%d row(s))",
        user_id,
        detected_date,
        len(rows),
    )


_BASELINE_COLUMNS: frozenset[str] = frozenset(
    {
        "user_id",
        "metric_date",
        "metric",
        "window_days",
        "mean_value",
        "stdev_value",
        "sample_n",
    }
)


def upsert_metric_baselines(cur: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    """Upsert ``metric_baselines`` rows (ON CONFLICT update means)."""
    if not rows:
        return
    insert_sql = (
        "INSERT INTO metric_baselines "
        "(user_id, metric_date, metric, window_days, mean_value, stdev_value, sample_n) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, metric_date, metric, window_days) DO UPDATE SET "
        "mean_value = EXCLUDED.mean_value, stdev_value = EXCLUDED.stdev_value, "
        "sample_n = EXCLUDED.sample_n, updated_at = NOW()"
    )
    for row in rows:
        unknown = set(row) - _BASELINE_COLUMNS
        if unknown:
            raise KeyError(f"metric_baselines row has unknown column(s): {sorted(unknown)}")
        cur.execute(
            insert_sql,
            (
                row["user_id"],
                row["metric_date"],
                row["metric"],
                row["window_days"],
                row.get("mean_value"),
                row.get("stdev_value"),
                row.get("sample_n"),
            ),
        )


_PATTERN_COLUMNS: frozenset[str] = frozenset(
    {
        "user_id",
        "metric_a",
        "metric_b",
        "lag_days",
        "correlation",
        "sample_n",
        "status",
        "description",
    }
)


def replace_metric_patterns(cur: Any, *, user_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
    """Replace all ``active`` patterns for a user with freshly computed rows."""
    cur.execute(
        "DELETE FROM metric_patterns WHERE user_id = %s AND status = %s",
        (user_id, "active"),
    )
    insert_sql = (
        "INSERT INTO metric_patterns "
        "(user_id, metric_a, metric_b, lag_days, correlation, sample_n, status, description) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, metric_a, metric_b, lag_days) DO UPDATE SET "
        "correlation = EXCLUDED.correlation, sample_n = EXCLUDED.sample_n, "
        "status = EXCLUDED.status, description = EXCLUDED.description, "
        "last_confirmed_at = NOW()"
    )
    for row in rows:
        unknown = set(row) - _PATTERN_COLUMNS
        if unknown:
            raise KeyError(f"metric_patterns row has unknown column(s): {sorted(unknown)}")
        cur.execute(
            insert_sql,
            (
                row["user_id"],
                row["metric_a"],
                row["metric_b"],
                row["lag_days"],
                row.get("correlation"),
                row.get("sample_n"),
                row.get("status", "active"),
                row.get("description"),
            ),
        )


_RUNNING_SESSION_COLUMNS: frozenset[str] = frozenset(
    {
        "user_id",
        "session_date",
        "run_type",
        "distance_km",
        "duration_min",
        "notes",
        "source",
        "source_id",
    }
)


def insert_running_session(cur: Any, row: Mapping[str, Any]) -> None:
    """Insert one running session (ON CONFLICT DO NOTHING)."""
    unknown = set(row) - _RUNNING_SESSION_COLUMNS
    if unknown:
        raise KeyError(f"running_sessions row has unknown column(s): {sorted(unknown)}")
    for key in ("user_id", "session_date", "run_type", "source", "source_id"):
        if row.get(key) is None:
            raise KeyError(f"running_sessions row missing required field {key!r}")
    cols = list(row.keys())
    values = [row[c] for c in cols]
    statement = sql.SQL(
        "INSERT INTO running_sessions ({cols}) VALUES ({ph}) "
        "ON CONFLICT (user_id, source, source_id) DO NOTHING"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        ph=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    cur.execute(statement, values)


_SCHEDULE_EXCEPTION_COLUMNS: frozenset[str] = frozenset(
    {
        "user_id",
        "start_date",
        "end_date",
        "affected_goal_types",
        "override_hint",
        "reason",
    }
)


_TRAINING_PHASE_COLUMNS = frozenset(
    {
        "user_id",
        "name",
        "phase_type",
        "start_date",
        "end_date",
        "notes",
        "target_notes",
        "is_active",
    }
)


def insert_training_phase(cur: Any, row: Mapping[str, Any]) -> None:
    """Insert a training phase row."""
    unknown = set(row) - _TRAINING_PHASE_COLUMNS
    if unknown:
        raise KeyError(f"training_phases row has unknown column(s): {sorted(unknown)}")
    for key in ("user_id", "name", "phase_type", "start_date", "end_date"):
        if row.get(key) is None:
            raise KeyError(f"training_phases row missing required field {key!r}")
    cur.execute(
        "INSERT INTO training_phases "
        "(user_id, name, phase_type, start_date, end_date, notes, target_notes, is_active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            row["user_id"],
            row["name"],
            row["phase_type"],
            row["start_date"],
            row["end_date"],
            row.get("notes"),
            row.get("target_notes"),
            row.get("is_active", True),
        ),
    )


_TRAINING_PHASE_UPDATE_COLUMNS = frozenset(
    {
        "name",
        "phase_type",
        "start_date",
        "end_date",
        "notes",
        "target_notes",
        "is_active",
    }
)

_JOURNAL_ENTRY_COLUMNS = frozenset(
    {
        "user_id",
        "entry_date",
        "category",
        "body",
    }
)


def update_training_phase(
    cur: Any,
    *,
    user_id: str,
    phase_id: str,
    updates: Mapping[str, Any],
) -> None:
    """Update one training phase row (coaching chat)."""
    unknown = set(updates) - _TRAINING_PHASE_UPDATE_COLUMNS
    if unknown:
        raise KeyError(f"training_phases update has unknown column(s): {sorted(unknown)}")
    if not updates:
        raise ValueError("updates required")
    start = updates.get("start_date")
    end = updates.get("end_date")
    if start is not None and end is not None and end < start:
        raise ValueError("end_date must be on or after start_date")
    assignments = ", ".join(f"{col} = %s" for col in updates)
    values = list(updates.values()) + [user_id, phase_id]
    cur.execute(
        f"UPDATE training_phases SET {assignments}, updated_at = NOW() "
        f"WHERE user_id = %s AND id = %s",
        values,
    )


def insert_journal_entry(cur: Any, row: Mapping[str, Any]) -> None:
    """Insert an athlete journal entry."""
    unknown = set(row) - _JOURNAL_ENTRY_COLUMNS
    if unknown:
        raise KeyError(f"athlete_journal_entries row has unknown column(s): {sorted(unknown)}")
    for key in ("user_id", "entry_date", "category", "body"):
        if row.get(key) is None:
            raise KeyError(f"athlete_journal_entries row missing required field {key!r}")
    cur.execute(
        "INSERT INTO athlete_journal_entries (user_id, entry_date, category, body) "
        "VALUES (%s, %s, %s, %s)",
        (row["user_id"], row["entry_date"], row["category"], row["body"]),
    )


def insert_schedule_exception(cur: Any, row: Mapping[str, Any]) -> None:
    """Insert a schedule exception row."""
    unknown = set(row) - _SCHEDULE_EXCEPTION_COLUMNS
    if unknown:
        raise KeyError(f"schedule_exceptions row has unknown column(s): {sorted(unknown)}")
    for key in ("user_id", "start_date", "end_date", "affected_goal_types"):
        if row.get(key) is None:
            raise KeyError(f"schedule_exceptions row missing required field {key!r}")
    cur.execute(
        "INSERT INTO schedule_exceptions "
        "(user_id, start_date, end_date, affected_goal_types, override_hint, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            row["user_id"],
            row["start_date"],
            row["end_date"],
            row["affected_goal_types"],
            row.get("override_hint"),
            row.get("reason"),
        ),
    )

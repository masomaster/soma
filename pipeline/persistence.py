"""Upserts for the Phase 6 analysis tables (``daily_health_metrics``,
``daily_features``, ``daily_briefings``).

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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
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
}

# JSONB columns are serialized before binding so callers can pass plain dicts.
_JSONB_COLUMNS = frozenset({"recommendations", "features_json", "anomalies"})


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

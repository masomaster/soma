"""Weekly signal job: ``metric_patterns`` + optional Sonnet ``llm_pattern`` rows."""

from __future__ import annotations

import logging
import os
from datetime import date, timezone
from datetime import datetime as _dt
from typing import Any

import psycopg2

from pipeline import clients
from pipeline import metric_patterns as metric_patterns_mod
from pipeline import persistence
from pipeline import weekly_pattern_scan as weekly_pattern_mod
from pipeline.lambda_secrets import resolve_lambda_secrets, resolve_soma_user_id
from pipeline.weekly_pattern_scan import DEFAULT_WEEKLY_MODEL

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(event["run_date"]) if event and event.get("run_date") else (
        _dt.now(timezone.utc).date()
    )
    db_url, anthropic_key, _ses = resolve_lambda_secrets()
    weekly_enabled = weekly_pattern_mod.weekly_scan_enabled(
        os.environ.get("ENABLE_WEEKLY_PATTERN_LLM")
    )
    weekly_llm = clients.anthropic_llm(
        anthropic_key,
        model=os.environ.get("WEEKLY_PATTERN_MODEL", DEFAULT_WEEKLY_MODEL),
        max_tokens=900,
    )

    conn = psycopg2.connect(db_url)
    summaries: list[dict[str, Any]] = []
    try:
        loaders = clients.build_db_loaders(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM user_settings")
            users = [r[0] for r in cur.fetchall()]

        for user_id in users:
            uid = str(user_id)
            try:
                window = list(loaders["load_daily_metrics_window"](uid, run_date))
                patterns = metric_patterns_mod.compute_metric_patterns(
                    user_id=uid, as_of=run_date, daily_metrics_history=window
                )
                with conn.cursor() as cur:
                    persistence.replace_metric_patterns(cur, user_id=uid, rows=patterns)
                    if weekly_enabled:
                        llm_patterns = weekly_pattern_mod.run_weekly_pattern_scan(
                            user_id=uid,
                            run_date=run_date,
                            daily_metrics=window,
                            llm=weekly_llm,
                        )
                        if llm_patterns:
                            rows = weekly_pattern_mod.build_llm_pattern_anomaly_rows(
                                user_id=uid,
                                detected_date=run_date,
                                patterns=llm_patterns,
                                model_used=os.environ.get("WEEKLY_PATTERN_MODEL", DEFAULT_WEEKLY_MODEL),
                            )
                            persistence.replace_llm_pattern_anomaly_events(
                                cur, user_id=uid, detected_date=run_date, rows=rows
                            )
                conn.commit()
                summaries.append({"user_id": uid, "ok": True, "patterns": len(patterns)})
            except Exception as exc:
                conn.rollback()
                logger.error("Weekly signal failed for %s: %s", uid, exc)
                summaries.append({"user_id": uid, "ok": False, "error": type(exc).__name__})
    finally:
        conn.close()

    return {"run_date": run_date.isoformat(), "results": summaries}

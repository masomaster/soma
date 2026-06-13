"""Thin Lambda entry point for the daily briefing pipeline.

Keeps logic out of the handler (see ``.cursor/rules/soma.mdc``): it wires
concrete clients to :func:`pipeline.orchestration.run_daily_pipeline` for each
active user and returns a small summary. The ``pipeline`` package + ``psycopg2``
are provided via a Lambda layer/container (see ``README.md``).

Environment variables (set by ``DailyBriefingPipeline`` / operator secrets):
    ENV                 local|staging|prod
    SOMA_RULES_PREFIX   /soma/{env}/   (SSM tree for per-user thresholds)
    DB_CONNECT_STRING   Postgres (Supabase) service-role connection string
    ANTHROPIC_API_KEY   Anthropic key
    BRIEFING_MODEL      (optional) override briefing model id
    SES_SENDER          verified SES From address
"""

from __future__ import annotations

import logging
import os
from datetime import date, timezone
from datetime import datetime as _dt
from typing import Any

import psycopg2

from pipeline import clients
from pipeline import persistence
from pipeline.briefing import DEFAULT_BRIEFING_MODEL
from pipeline.delivery import deliver_briefing
from pipeline.orchestration import DailyPipelineIO, run_daily_pipeline
from pipeline.rules import load_thresholds
from pipeline.settings import get_environment

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    env = get_environment()
    run_date = date.fromisoformat(event["run_date"]) if event and event.get("run_date") else (
        _dt.now(timezone.utc).date()
    )

    llm = clients.anthropic_llm(
        os.environ["ANTHROPIC_API_KEY"],
        model=os.environ.get("BRIEFING_MODEL", DEFAULT_BRIEFING_MODEL),
    )
    send_email = clients.ses_email_sender(os.environ["SES_SENDER"])
    get_parameters = clients.ssm_threshold_loader()

    conn = psycopg2.connect(os.environ["DB_CONNECT_STRING"])
    summaries: list[dict[str, Any]] = []

    def _persister(table: str):
        def _persist(row: dict[str, Any]) -> None:
            with conn.cursor() as cur:
                persistence.upsert_row(cur, table, row)

        return _persist

    try:
        loaders = clients.build_db_loaders(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, email FROM user_settings")
            users = cur.fetchall()

        for user_id, email in users:
            # Each user runs in its own transaction so one user's DB error cannot
            # poison the next user's writes, and we commit BEFORE emailing so an
            # irreversible email is never sent for rows that failed to persist.
            try:
                thresholds = load_thresholds(
                    env=env.value, user_id=str(user_id), get_parameters=get_parameters
                )
                io = DailyPipelineIO(
                    llm=llm,
                    thresholds=thresholds,
                    to_address=email,
                    deliver=None,  # delivered after commit, below
                    persist_daily_metrics=_persister("daily_health_metrics"),
                    persist_features=_persister("daily_features"),
                    persist_briefing=_persister("daily_briefings"),
                    **loaders,
                )
                result = run_daily_pipeline(user_id=str(user_id), run_date=run_date, io=io)
                conn.commit()

                delivered = False
                if result.ok and result.briefing is not None:
                    deliver_briefing(
                        result.briefing, env=env, send_email=send_email, to_address=email
                    )
                    delivered = True
                summaries.append(
                    {
                        "user_id": str(user_id),
                        "ok": result.ok,
                        "delivered": delivered,
                        "flags": [f.code for f in result.flags],
                    }
                )
            except Exception as exc:
                conn.rollback()
                logger.exception("Daily pipeline failed for user %s", user_id)
                summaries.append(
                    {"user_id": str(user_id), "ok": False, "error": type(exc).__name__}
                )
    finally:
        conn.close()

    logger.info("Daily pipeline finished for %d user(s)", len(summaries))
    return {"run_date": run_date.isoformat(), "results": summaries}

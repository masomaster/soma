"""Concrete IO clients for the briefing Lambda (Anthropic, SES, SSM, Postgres).

These adapt external services to the small injected interfaces used by
:mod:`pipeline.orchestration` / :mod:`pipeline.briefing` / :mod:`pipeline.delivery`.
``boto3`` is imported lazily (it is a Lambda-provided dependency, not a package
requirement) so importing this module — and unit-testing the Anthropic client —
needs no AWS SDK. ``psycopg2`` is already a package dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.briefing import LLMClient
from pipeline.features import CHRONIC_WINDOW_DAYS

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def anthropic_llm(
    api_key: str,
    *,
    model: str,
    max_tokens: int = 600,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> LLMClient:
    """Return an :data:`~pipeline.briefing.LLMClient` backed by the Anthropic Messages API.

    ``urlopen`` is injectable so the request/response handling is unit-testable
    without network access.
    """

    def _call(system: str, user_prompt: str) -> str:
        body = json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_prompt}],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            err_raw = ""
            try:
                err_raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            err_raw = err_raw.strip()
            detail = err_raw[:1200] if err_raw else ""
            try:
                parsed = json.loads(err_raw)
                inner = parsed.get("error")
                if isinstance(inner, dict) and inner.get("message"):
                    detail = str(inner["message"])
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            raise RuntimeError(
                f"Anthropic Messages API HTTP {exc.code} (model={model!r})"
                + (f": {detail}" if detail else "")
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Anthropic request failed: {exc}") from None

        payload = json.loads(raw)
        blocks = payload.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("Anthropic response missing 'content' blocks")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if not text.strip():
            raise ValueError("Anthropic response contained no text")
        return text

    return _call


def ses_email_sender(
    sender: str,
    *,
    region: str | None = None,
    client: Any = None,
) -> Callable[..., str]:
    """Return ``(to, subject, body, *, html_body=...) -> message_id`` backed by SES."""
    if client is None:
        import boto3  # lazy: Lambda-provided, not a package dependency

        client = boto3.client("ses", region_name=region)

    def _send(to_address: str, subject: str, body: str, html_body: str | None = None) -> str:
        msg_body: dict[str, Any] = {"Text": {"Data": body, "Charset": "UTF-8"}}
        if html_body:
            msg_body["Html"] = {"Data": html_body, "Charset": "UTF-8"}
        resp = client.send_email(
            Source=sender,
            Destination={"ToAddresses": [to_address]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": msg_body,
            },
        )
        return resp.get("MessageId", "")

    return _send


def ssm_threshold_loader(*, client: Any = None) -> Callable[[str], dict[str, str]]:
    """Return a getter mapping an SSM path prefix to ``{full_name: value}``."""
    if client is None:
        import boto3  # lazy

        client = boto3.client("ssm")

    def _get(prefix: str) -> dict[str, str]:
        out: dict[str, str] = {}
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
            for param in page.get("Parameters", []):
                out[param["Name"]] = param["Value"]
        return out

    return _get


def build_db_loaders(conn: Any) -> dict[str, Callable[..., Sequence[Mapping[str, Any]]]]:
    """Build the orchestrator's load_* callables from a psycopg2 connection.

    Uses a ``RealDictCursor`` so rows come back as dicts matching the column
    names the feature functions expect. Read-only ``SELECT``s scoped by
    ``user_id`` (service-role connection; RLS bypassed).
    """
    from psycopg2.extras import RealDictCursor

    def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def load_biometrics_today(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT metric, value FROM biometrics WHERE user_id = %s AND event_date = %s",
            (user_id, d),
        )

    def load_daily_metrics_window(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT * FROM daily_health_metrics "
            "WHERE user_id = %s AND metric_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=CHRONIC_WINDOW_DAYS - 1), d),
        )

    def load_daily_features_window(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT * FROM daily_features "
            "WHERE user_id = %s AND feature_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=CHRONIC_WINDOW_DAYS - 1), d),
        )

    def load_strength_events(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT event_date, set_type, reps, weight_lbs, rpe FROM strength_events "
            "WHERE user_id = %s AND event_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=CHRONIC_WINDOW_DAYS - 1), d),
        )

    def load_cardio_events(user_id: str, d: date) -> list[dict[str, Any]]:
        # activity_type + distance_miles are required by run detection
        # (count_run_sessions_7d, goal_progress._running_done, mileage_ramp);
        # omitting them makes every cardio row look like a non-run so runs,
        # running-goal completion, and weekly run distance all read as zero.
        return _query(
            "SELECT event_date, activity_type, distance_miles, duration_min, session_rpe "
            "FROM cardio_events WHERE user_id = %s AND event_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=CHRONIC_WINDOW_DAYS - 1), d),
        )

    def load_active_patterns(user_id: str, d: date) -> list[dict[str, Any]]:
        del d  # patterns are user-scoped; ``last_confirmed_at`` orders recency
        return _query(
            "SELECT metric_a, metric_b, lag_days, correlation, sample_n, status, description "
            "FROM metric_patterns WHERE user_id = %s AND status = %s "
            "ORDER BY last_confirmed_at DESC NULLS LAST LIMIT 12",
            (user_id, "active"),
        )

    def load_goals(user_id: str, d: date) -> list[dict[str, Any]]:
        del d
        return _query(
            "SELECT goal_type, target_min, target_max, target_label, period, is_active, "
            "effective_from, effective_until, notes FROM goals "
            "WHERE user_id = %s AND is_active = TRUE",
            (user_id,),
        )

    def load_running_sessions(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT session_date, run_type, distance_km, duration_min, source "
            "FROM running_sessions WHERE user_id = %s AND session_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=CHRONIC_WINDOW_DAYS - 1), d),
        )

    def load_schedule_exceptions(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT start_date, end_date, affected_goal_types, override_hint, reason "
            "FROM schedule_exceptions WHERE user_id = %s AND end_date >= %s",
            (user_id, d - timedelta(days=14)),
        )

    def load_interventions(user_id: str, d: date) -> list[dict[str, Any]]:
        return _query(
            "SELECT event_date, category, description FROM interventions "
            "WHERE user_id = %s AND event_date BETWEEN %s AND %s",
            (user_id, d - timedelta(days=7), d + timedelta(days=7)),
        )

    return {
        "load_biometrics_today": load_biometrics_today,
        "load_daily_metrics_window": load_daily_metrics_window,
        "load_daily_features_window": load_daily_features_window,
        "load_strength_events": load_strength_events,
        "load_cardio_events": load_cardio_events,
        "load_active_patterns": load_active_patterns,
        "load_goals": load_goals,
        "load_running_sessions": load_running_sessions,
        "load_schedule_exceptions": load_schedule_exceptions,
        "load_interventions": load_interventions,
    }

"""Optional weekly Sonnet narrative pattern scan (Phase 8c).

Runs only on **Sunday** when ``ENABLE_WEEKLY_PATTERN_LLM`` is truthy. The model
reads compact **daily aggregates** (not raw events) and returns short pattern
hypotheses. Numeric outliers remain :mod:`pipeline.stat_anomalies` — this layer
is narrative context only.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.briefing import LLMClient
from pipeline.features import as_date

logger = logging.getLogger(__name__)

DEFAULT_WEEKLY_MODEL = "claude-sonnet-4-20250514"
LOOKBACK_DAYS = 60
MIN_HISTORY_DAYS = 14

SYSTEM_GUIDELINES = (
    "You are Soma's weekly pattern analyst. You receive COMPACT daily aggregates "
    "for one athlete over about 60 days. Suggest at most THREE plausible "
    "cross-metric patterns or lag relationships worth watching next week. Do NOT "
    "invent numbers, do NOT contradict the aggregates, and do NOT give medical "
    "diagnoses. Reply with a JSON array only, e.g. "
    '[{"title": "...", "description": "...", "confidence": "low"}]'
)

_SUMMARY_METRICS = (
    "hrv_rmssd",
    "sleep_hours",
    "resting_hr",
    "steps",
    "body_weight_lbs",
    "sleep_score",
)


def weekly_scan_enabled(raw: str | None) -> bool:
    """Return True when ``ENABLE_WEEKLY_PATTERN_LLM`` is set to a truthy string."""
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_weekly_summary_payload(
    daily_metrics: Sequence[Mapping[str, Any]],
    *,
    run_date: date,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Compact JSON-serializable window for the weekly LLM (no raw events)."""
    start = run_date - timedelta(days=lookback_days)
    days: list[dict[str, Any]] = []
    for row in daily_metrics:
        d = as_date(row.get("metric_date"))
        if d is None or d < start or d > run_date:
            continue
        item: dict[str, Any] = {"metric_date": d.isoformat()}
        for key in _SUMMARY_METRICS:
            if row.get(key) is not None:
                item[key] = row[key]
        if len(item) > 1:
            days.append(item)
    days.sort(key=lambda x: x["metric_date"])
    return {"as_of": run_date.isoformat(), "lookback_days": lookback_days, "days": days}


def _parse_pattern_response(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    # Allow optional markdown fence around JSON.
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```\s*$", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [{"title": "weekly_pattern", "description": text[:2000], "confidence": "low"}]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data[:3]:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        desc = item.get("description")
        if not isinstance(title, str) or not isinstance(desc, str):
            continue
        conf = item.get("confidence")
        confidence = conf if isinstance(conf, str) else "low"
        out.append({"title": title.strip(), "description": desc.strip(), "confidence": confidence})
    return out


def build_llm_pattern_anomaly_rows(
    *,
    user_id: str,
    detected_date: date,
    patterns: Sequence[Mapping[str, Any]],
    model_used: str,
) -> list[dict[str, Any]]:
    """Map weekly LLM patterns to ``anomaly_events`` rows (``anomaly_type = llm_pattern``)."""
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(patterns):
        title = str(p.get("title", "pattern")).strip() or "pattern"
        desc = str(p.get("description", "")).strip()
        if not desc:
            continue
        full_desc = f"{title}: {desc}"
        if len(full_desc) > 500:
            full_desc = full_desc[:497] + "..."
        rows.append(
            {
                "user_id": user_id,
                "detected_date": detected_date,
                "metric": None,
                "anomaly_type": "llm_pattern",
                "description": full_desc,
                "severity": "info",
                "context_json": {
                    "title": title,
                    "description": desc,
                    "confidence": p.get("confidence"),
                    "model_used": model_used,
                    "ordinal": i,
                },
            }
        )
    return rows


def run_weekly_pattern_scan(
    *,
    user_id: str,
    run_date: date,
    daily_metrics: Sequence[Mapping[str, Any]],
    llm: LLMClient,
    model: str = DEFAULT_WEEKLY_MODEL,
) -> list[dict[str, Any]] | None:
    """On Sunday, call Sonnet with aggregates; return parsed patterns or ``None`` if skipped."""
    if run_date.weekday() != 6:
        return None
    summary = build_weekly_summary_payload(daily_metrics, run_date=run_date)
    if len(summary["days"]) < MIN_HISTORY_DAYS:
        logger.info("Weekly pattern scan skipped for %s: only %d history days", user_id, len(summary["days"]))
        return None
    prompt = json.dumps(summary, indent=2, sort_keys=True)
    raw = llm(SYSTEM_GUIDELINES, prompt).strip()
    patterns = _parse_pattern_response(raw)
    logger.info("Weekly pattern scan for %s produced %d pattern(s)", user_id, len(patterns))
    return patterns

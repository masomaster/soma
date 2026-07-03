"""Run weekly correlation + optional LLM pattern scan (Phase 8c)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from pipeline import metric_patterns as metric_patterns_mod
from pipeline import weekly_pattern_scan as weekly_pattern_mod
from pipeline.briefing import LLMClient

logger = logging.getLogger(__name__)


def run_weekly_signal_job(
    *,
    user_id: str,
    run_date: date,
    daily_metrics_window: Sequence[Mapping[str, Any]],
    persist_patterns: Callable[[list[dict[str, Any]]], None],
    daily_features_window: Sequence[Mapping[str, Any]] | None = None,
    weekly_llm: LLMClient | None = None,
    persist_llm_anomalies: Callable[[list[dict[str, Any]]], None] | None = None,
    weekly_enabled: bool = False,
    model_used: str = weekly_pattern_mod.DEFAULT_WEEKLY_MODEL,
) -> dict[str, Any]:
    """Recompute ``metric_patterns`` (within- and cross-table); optional Sunday Sonnet.

    When ``daily_features_window`` is supplied, cross-table correlations between
    recovery metrics (``daily_health_metrics``) and training outcomes
    (``daily_features``) are computed alongside the within-table pairs so the
    coaching chat can answer questions like sleep vs cardio/strength gains.
    """
    patterns = metric_patterns_mod.compute_all_metric_patterns(
        user_id=user_id,
        as_of=run_date,
        daily_metrics_history=daily_metrics_window,
        daily_features_history=daily_features_window,
    )
    persist_patterns(patterns)
    llm_count = 0
    if weekly_enabled and weekly_llm is not None and persist_llm_anomalies is not None:
        llm_patterns = weekly_pattern_mod.run_weekly_pattern_scan(
            user_id=user_id,
            run_date=run_date,
            daily_metrics=daily_metrics_window,
            llm=weekly_llm,
            model=model_used,
        )
        if llm_patterns:
            rows = weekly_pattern_mod.build_llm_pattern_anomaly_rows(
                user_id=user_id,
                detected_date=run_date,
                patterns=llm_patterns,
                model_used=model_used,
            )
            persist_llm_anomalies(rows)
            llm_count = len(rows)
    logger.info(
        "Weekly signal job user=%s patterns=%d llm_rows=%d", user_id, len(patterns), llm_count
    )
    return {"ok": True, "patterns": len(patterns), "llm_pattern_rows": llm_count}

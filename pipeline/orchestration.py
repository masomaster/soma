"""Phase 5 orchestration: a single daily pipeline with ordered, isolated steps.

Per ``docs/plans/implementation-plan.md`` Phase 5 we run **one daily pipeline**
(single scheduled start) whose steps execute in a fixed order rather than racing
several tight cron jobs:

    rollup today's metrics -> compute features -> evaluate rules
        -> goal snapshot -> statistical signals -> generate briefing -> deliver

All IO (DB loads/writes, the LLM, email) is injected via :class:`DailyPipelineIO`
so the orchestrator itself is pure control-flow and fully unit-testable; the
Lambda handler builds the concrete IO from boto3 / psycopg2 / Anthropic. Each
step is wrapped so a failure is recorded and stops the run cleanly with a partial
:class:`PipelineResult` instead of a bare traceback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pipeline import features as features_mod
from pipeline import metric_baselines as metric_baselines_mod
from pipeline import metric_patterns as metric_patterns_mod
from pipeline import metrics_summary as metrics_summary_mod
from pipeline import rules as rules_mod
from pipeline import goal_progress as goal_progress_mod
from pipeline import stat_anomalies as stat_anomalies_mod
from pipeline.briefing import Briefing, LLMClient, generate_briefing
from pipeline.mileage_ramp import iso_week_start
from pipeline.rules import Flag

logger = logging.getLogger(__name__)

Row = Mapping[str, Any]


@dataclass(slots=True)
class DailyPipelineIO:
    """Injected boundaries. DB writers/deliver are optional (skipped if ``None``)."""

    llm: LLMClient
    load_biometrics_today: Callable[[str, date], Sequence[Row]]
    load_daily_metrics_window: Callable[[str, date], Sequence[Row]]
    load_strength_events: Callable[[str, date], Sequence[Row]]
    load_cardio_events: Callable[[str, date], Sequence[Row]]
    persist_daily_metrics: Callable[[Row], None] | None = None
    persist_features: Callable[[Row], None] | None = None
    persist_briefing: Callable[[Row], None] | None = None
    persist_statistical_anomalies: Callable[[str, date, dict[str, Any]], None] | None = None
    persist_metric_baselines: Callable[[Sequence[Row]], None] | None = None
    load_active_patterns: Callable[[str, date], Sequence[Row]] | None = None
    load_guidelines: Callable[[str], Any] | None = None
    load_goals: Callable[[str, date], Sequence[Row]] | None = None
    load_running_sessions: Callable[[str, date], Sequence[Row]] | None = None
    load_schedule_exceptions: Callable[[str, date], Sequence[Row]] | None = None
    load_interventions: Callable[[str, date], Sequence[Row]] | None = None
    persist_goal_snapshot: Callable[[Row], None] | None = None
    persist_weekly_summary: Callable[[Row], None] | None = None
    deliver: Callable[[Briefing], dict[str, Any]] | None = None
    thresholds: Mapping[str, float] = field(default_factory=dict)
    to_address: str | None = None


@dataclass(slots=True)
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class PipelineResult:
    user_id: str
    run_date: date
    steps: list[StepResult] = field(default_factory=list)
    daily_metrics: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    flags: list[Flag] = field(default_factory=list)
    stat_signals: dict[str, Any] | None = None
    goal_snapshot: dict[str, Any] | None = None
    daily_metrics_window: list[dict[str, Any]] | None = None
    briefing: Briefing | None = None
    delivery: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def run_daily_pipeline(
    *,
    user_id: str,
    run_date: date,
    io: DailyPipelineIO,
) -> PipelineResult:
    """Execute the ordered daily pipeline for one user; never raises on step failure."""
    result = PipelineResult(user_id=user_id, run_date=run_date)
    thresholds = {**rules_mod.DEFAULT_THRESHOLDS, **dict(io.thresholds)}

    def step(name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
            result.steps.append(StepResult(name, ok=True))
            return True
        except Exception as exc:  # isolate: one bad step shouldn't crash the scheduler
            logger.error(
                "Pipeline step %r failed for user %s: %s: %s",
                name,
                user_id,
                type(exc).__name__,
                exc,
                exc_info=False,
            )
            result.steps.append(StepResult(name, ok=False, detail=f"{type(exc).__name__}: {exc}"))
            return False

    def do_rollup() -> None:
        today = io.load_biometrics_today(user_id, run_date)
        result.daily_metrics = features_mod.rollup_daily_health_metrics(
            today, user_id=user_id, metric_date=run_date
        )
        if io.persist_daily_metrics is not None:
            io.persist_daily_metrics(result.daily_metrics)

    def do_features() -> None:
        window = list(io.load_daily_metrics_window(user_id, run_date))
        # Ensure today's freshly-rolled metrics are part of the feature window.
        if result.daily_metrics is not None and not any(
            features_mod.as_date(m.get("metric_date")) == run_date for m in window
        ):
            window.append(result.daily_metrics)
        result.features = features_mod.compute_daily_features(
            user_id=user_id,
            feature_date=run_date,
            strength_events=io.load_strength_events(user_id, run_date),
            cardio_events=io.load_cardio_events(user_id, run_date),
            daily_metrics=window,
            target_sleep_hours=thresholds["target_sleep_hours"],
            hrv_suppressed_ratio=thresholds["hrv_suppressed_ratio"],
            max_acute_chronic_ratio=thresholds["max_acute_chronic_ratio"],
        )
        if io.persist_features is not None:
            io.persist_features(result.features)
        result.daily_metrics_window = window

    def do_rules() -> None:
        result.flags = rules_mod.evaluate(
            features=result.features or {},
            daily_metrics=result.daily_metrics or {},
            thresholds=thresholds,
        )

    def do_goal_snapshot() -> None:
        if io.load_goals is None:
            return
        goals = list(io.load_goals(user_id, run_date))
        running = (
            list(io.load_running_sessions(user_id, run_date))
            if io.load_running_sessions is not None
            else []
        )
        exceptions = (
            list(io.load_schedule_exceptions(user_id, run_date))
            if io.load_schedule_exceptions is not None
            else []
        )
        interventions = (
            list(io.load_interventions(user_id, run_date))
            if io.load_interventions is not None
            else []
        )
        strength = io.load_strength_events(user_id, run_date)
        cardio = io.load_cardio_events(user_id, run_date)
        result.goal_snapshot = goal_progress_mod.build_daily_goal_snapshot(
            user_id=user_id,
            run_date=run_date,
            goals=goals,
            strength_events=strength,
            running_sessions=running,
            cardio_events=cardio,
            exceptions=exceptions,
            interventions=interventions,
        )
        if io.persist_goal_snapshot is not None:
            io.persist_goal_snapshot(result.goal_snapshot)
        if io.persist_weekly_summary is not None:
            week_start = iso_week_start(run_date)
            summary = goal_progress_mod.compute_weekly_activity_summary(
                user_id=user_id,
                week_start=week_start,
                strength_events=strength,
                running_sessions=running,
                cardio_events=cardio,
            )
            io.persist_weekly_summary(summary)

    def do_stat_signals() -> None:
        window = result.daily_metrics_window
        if window is None:
            window = list(io.load_daily_metrics_window(user_id, run_date))
            if result.daily_metrics is not None and not any(
                features_mod.as_date(m.get("metric_date")) == run_date for m in window
            ):
                window.append(result.daily_metrics)
        result.stat_signals = stat_anomalies_mod.compute_statistical_signals(
            feature_date=run_date,
            daily_metrics_history=window,
            today_metrics=result.daily_metrics or {},
        )
        if io.persist_statistical_anomalies is not None:
            io.persist_statistical_anomalies(user_id, run_date, result.stat_signals)
        if window and io.persist_metric_baselines is not None:
            baseline_rows = metric_baselines_mod.compute_metric_baselines(
                user_id=user_id,
                metric_date=run_date,
                daily_metrics_history=window,
            )
            io.persist_metric_baselines(baseline_rows)

    def do_briefing() -> None:
        active_patterns: list[str] = []
        if io.load_active_patterns is not None:
            active_patterns = metric_patterns_mod.active_pattern_summaries(
                io.load_active_patterns(user_id, run_date)
            )
        guidelines = io.load_guidelines(user_id) if io.load_guidelines is not None else None
        running = (
            list(io.load_running_sessions(user_id, run_date))
            if io.load_running_sessions is not None
            else []
        )
        run_sessions_7d = metrics_summary_mod.count_run_sessions_7d(
            io.load_cardio_events(user_id, run_date),
            running,
            as_of=run_date,
        )
        result.briefing = generate_briefing(
            user_id=user_id,
            feature_date=run_date,
            flags=result.flags,
            features=result.features or {},
            llm=io.llm,
            daily_metrics=result.daily_metrics or {},
            stat_signals=result.stat_signals,
            active_patterns=active_patterns,
            goal_snapshot=result.goal_snapshot,
            guidelines=guidelines,
            run_sessions_7d=run_sessions_7d,
        )
        if io.persist_briefing is not None:
            io.persist_briefing(result.briefing.to_row())

    def do_deliver() -> None:
        if io.deliver is not None and result.briefing is not None:
            result.delivery = io.deliver(result.briefing)

    # Ordered, dependency-respecting execution. Stop at the first hard failure.
    if not step("rollup_metrics", do_rollup):
        return result
    if not step("compute_features", do_features):
        return result
    # Rules failure is fatal: never send a falsely "all clear" briefing that
    # silently dropped the deterministic flags.
    if not step("evaluate_rules", do_rules):
        return result
    step("goal_snapshot", do_goal_snapshot)
    if not step("compute_stat_signals", do_stat_signals):
        return result
    if not step("generate_briefing", do_briefing):
        return result
    step("deliver", do_deliver)
    return result

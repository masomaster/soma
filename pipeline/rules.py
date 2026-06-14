"""Phase 6 deterministic rules engine (Option A: hand-coded + externalized thresholds).

Thresholds are **never hardcoded in business logic** (see ``.cursor/rules/soma.mdc``):
they live in SSM Parameter Store under ``/soma/{env}/{user_id}/rules/`` and are
loaded via an injectable getter so this module stays pure and unit-testable.
:func:`evaluate` turns features + daily metrics into a list of :class:`Flag`
objects; the LLM later narrates these pre-computed conclusions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Threshold name -> default value. Operators override per user in SSM; these
# defaults keep local/dev runs working with no Parameter Store access.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "target_sleep_hours": 8.0,
    "min_sleep_hours": 6.0,
    "max_sleep_debt_7d": 5.0,
    "hrv_suppressed_ratio": 0.85,
    "max_hrv_suppressed_days": 2,
    "max_acute_chronic_ratio": 1.5,
    "min_readiness_score": 60.0,
}

# Severity ordering for sorting / "worst first" presentation.
_SEVERITY_RANK = {"info": 0, "warning": 1, "alert": 2}


@dataclass(frozen=True, slots=True)
class Flag:
    """A single deterministic finding the briefing should narrate."""

    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def rules_ssm_prefix(env: str, user_id: str) -> str:
    """Canonical SSM prefix for a user's rule thresholds (trailing slash)."""
    return f"/soma/{env}/{user_id}/rules/"


def load_thresholds(
    *,
    env: str,
    user_id: str,
    get_parameters: Callable[[str], Mapping[str, str]] | None = None,
) -> dict[str, float]:
    """Return thresholds, overlaying SSM values (if any) on :data:`DEFAULT_THRESHOLDS`.

    ``get_parameters`` is injected (e.g. wraps boto3 ``ssm.get_parameters_by_path``)
    and maps the :func:`rules_ssm_prefix` to ``{name: value}``. Unknown names are
    ignored; non-numeric values are skipped with a warning so one bad parameter
    never breaks the run.
    """
    thresholds = dict(DEFAULT_THRESHOLDS)
    if get_parameters is None:
        return thresholds
    prefix = rules_ssm_prefix(env, user_id)
    raw = get_parameters(prefix)
    for name, value in raw.items():
        key = name.rsplit("/", 1)[-1]
        if key not in DEFAULT_THRESHOLDS:
            logger.warning("Ignoring unknown rule threshold %r from SSM", key)
            continue
        try:
            thresholds[key] = float(value)
        except (TypeError, ValueError):
            logger.warning("Non-numeric SSM threshold %s=%r; keeping default", key, value)
    return thresholds


def _f(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evaluate(
    *,
    features: Mapping[str, Any],
    daily_metrics: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> list[Flag]:
    """Evaluate the rule set against today's features (and optional same-day metrics).

    Returns flags sorted worst-severity first. ``daily_metrics`` is the wide row
    for ``feature_date`` (used for same-day signals like last night's sleep).
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    metrics = daily_metrics or {}
    flags: list[Flag] = []

    if (
        "recovery_sleep_days_7d" in features
        and "recovery_hrv_days_7d" in features
        and int(features.get("recovery_sleep_days_7d") or 0) == 0
        and int(features.get("recovery_hrv_days_7d") or 0) == 0
    ):
        flags.append(
            Flag(
                code="SPARSE_RECOVERY_DATA",
                severity="info",
                message=(
                    "No sleep or HRV rows in the last 7 days — recovery signals are unavailable; "
                    "do not infer sleep debt or HRV trends."
                ),
                evidence={
                    "recovery_sleep_days_7d": int(features.get("recovery_sleep_days_7d") or 0),
                    "recovery_hrv_days_7d": int(features.get("recovery_hrv_days_7d") or 0),
                },
            )
        )

    last_night_sleep = _f(metrics, "sleep_hours")
    if last_night_sleep is not None and last_night_sleep < th["min_sleep_hours"]:
        flags.append(
            Flag(
                code="LOW_SLEEP",
                severity="warning",
                message=(
                    f"Last night's sleep was {last_night_sleep:.1f}h, "
                    f"below the {th['min_sleep_hours']:.0f}h floor."
                ),
                evidence={"sleep_hours": last_night_sleep, "min_sleep_hours": th["min_sleep_hours"]},
            )
        )

    sleep_debt = _f(features, "sleep_debt_7d")
    if sleep_debt is not None and sleep_debt > th["max_sleep_debt_7d"]:
        flags.append(
            Flag(
                code="HIGH_SLEEP_DEBT",
                severity="warning",
                message=(
                    f"7-day sleep debt is {sleep_debt:.1f}h "
                    f"(over the {th['max_sleep_debt_7d']:.0f}h limit)."
                ),
                evidence={"sleep_debt_7d": sleep_debt, "max_sleep_debt_7d": th["max_sleep_debt_7d"]},
            )
        )

    suppressed = _f(features, "hrv_suppressed_days")
    if suppressed is not None and suppressed > th["max_hrv_suppressed_days"]:
        flags.append(
            Flag(
                code="LOW_HRV",
                severity="alert",
                message=(
                    f"HRV has been suppressed on {int(suppressed)} of the last 7 days "
                    "— recovery is lagging."
                ),
                evidence={
                    "hrv_suppressed_days": suppressed,
                    "max_hrv_suppressed_days": th["max_hrv_suppressed_days"],
                },
            )
        )

    acwr = _f(features, "acute_chronic_ratio")
    if acwr is not None and acwr > th["max_acute_chronic_ratio"]:
        flags.append(
            Flag(
                code="HIGH_TRAINING_LOAD",
                severity="alert",
                message=(
                    f"Acute:chronic training load is {acwr:.2f}, above the "
                    f"{th['max_acute_chronic_ratio']:.1f} injury-risk threshold."
                ),
                evidence={
                    "acute_chronic_ratio": acwr,
                    "max_acute_chronic_ratio": th["max_acute_chronic_ratio"],
                },
            )
        )

    readiness = _f(features, "overall_readiness_score")
    if readiness is not None and readiness < th["min_readiness_score"]:
        flags.append(
            Flag(
                code="LOW_READINESS",
                severity="warning",
                message=(
                    f"Overall readiness is {readiness:.0f}/100, below the "
                    f"{th['min_readiness_score']:.0f} target — consider an easier day."
                ),
                evidence={"overall_readiness_score": readiness, "min_readiness_score": th["min_readiness_score"]},
            )
        )

    flags.sort(key=lambda fl: _SEVERITY_RANK.get(fl.severity, 0), reverse=True)
    return flags

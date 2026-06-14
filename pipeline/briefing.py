"""Phase 6 briefing synthesis: turn pre-computed signals into a coaching note.

The LLM **narrates conclusions that were already computed** (flags + features) —
it does not reason over raw events (see ``.cursor/rules/soma.mdc``). The model
client is injected as a simple ``Callable`` so this module has no hard dependency
on Anthropic and is fully unit-testable; the Lambda wires in a Haiku-backed
client. The returned :class:`Briefing` maps onto the ``daily_briefings`` table.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pipeline.rules import Flag

logger = logging.getLogger(__name__)

# Pinned snapshot ID (see Anthropic model deprecations). Aliases like
# ``claude-haiku-4-5`` also work; avoid retired IDs such as ``claude-3-5-haiku-latest``.
DEFAULT_BRIEFING_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_GUIDELINES = (
    "You are Soma, a concise personal health coach. You are given PRE-COMPUTED "
    "signals (flags) and numeric features for one athlete. Narrate and prioritize "
    "those signals into a short, actionable morning briefing. Do NOT invent data, "
    "do NOT contradict the flags, and do NOT perform your own statistical analysis "
    "of raw history — only explain and act on what you are given. Lead with the "
    "most severe flag. If SPARSE_RECOVERY_DATA is present, do not describe sleep "
    "debt, sleep quality, or HRV trends. If recovery_sleep_days_7d is 0, do not "
    "describe weekly sleep debt or multi-day sleep trends (last night in TODAY'S "
    "METRICS may still be cited if flags mention it). If recovery_hrv_days_7d is 0, "
    "do not describe HRV trends. If acute_chronic_ratio is null, say load ratio was "
    "not computed (usually little cardio in the 28-day window) — do not call it a "
    "spike or injury risk from ACWR. If overall_readiness_score is null, say "
    "readiness could not be scored from recovery data and focus on training load. "
    "When training_load_* or effort_unified_index_* appear in FEATURES, treat "
    "training_load_* as modality-split external exposure (minutes or US short tons) "
    "and effort_unified_index_* as a heuristic combined trend—not HR TRIMP or "
    "clinical stress. "
    "Use plain sentences; at most light Markdown (bold, short bullets). "
    "Keep it under 150 words, warm but direct."
)

# LLM client contract: given (system, user_prompt) return the assistant text.
LLMClient = Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class Briefing:
    """A synthesized daily briefing, shaped for ``daily_briefings``."""

    user_id: str
    briefing_date: date
    coaching_note: str
    flags: list[str]
    features_json: dict[str, Any]
    model_used: str

    def to_row(self) -> dict[str, Any]:
        """Return a dict matching ``daily_briefings`` columns for upsert."""
        return {
            "user_id": self.user_id,
            "briefing_date": self.briefing_date,
            "flags": self.flags,
            "features_json": self.features_json,
            "coaching_note": self.coaching_note,
            "model_used": self.model_used,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


def build_prompt(
    *,
    feature_date: date,
    flags: Sequence[Flag],
    features: Mapping[str, Any],
    daily_metrics: Mapping[str, Any] | None = None,
) -> str:
    """Render the user prompt: the pre-computed flags + features the model must narrate."""
    flag_lines = (
        "\n".join(f"- [{f.severity.upper()}] {f.code}: {f.message}" for f in flags)
        if flags
        else "- None. All monitored signals are within normal ranges."
    )
    feature_blob = json.dumps(
        {k: _jsonable(v) for k, v in features.items() if v is not None},
        indent=2,
        sort_keys=True,
    )
    metrics_blob = json.dumps(
        {k: _jsonable(v) for k, v in (daily_metrics or {}).items() if v is not None},
        indent=2,
        sort_keys=True,
    )
    return (
        f"Date: {feature_date.isoformat()}\n\n"
        f"FLAGS (pre-computed, narrate these in priority order):\n{flag_lines}\n\n"
        f"FEATURES (rolling computed metrics):\n{feature_blob}\n\n"
        f"TODAY'S METRICS:\n{metrics_blob}\n\n"
        "UNITS / INTERPRETATION (do not contradict):\n"
        "- strength_tonnage_7d is US short tons (2000 lb): sum over the window of "
        "(reps x weight_lbs) / 2000. Do not call it \"metric tonnes\" unless you "
        "explicitly convert.\n"
        "- recovery_sleep_days_7d / recovery_hrv_days_7d count calendar days in the "
        "7-day window with at least one observation. When both are 0, recovery "
        "was not observed — do not fill in sleep or HRV narrative.\n"
        "- When recovery_sleep_days_7d is 0 but recovery_hrv_days_7d is not, weekly "
        "sleep debt and sleep trends are not supported by the pipeline — stick to "
        "HRV and training signals (and same-day metrics only if a flag references them).\n"
        "- When recovery_hrv_days_7d is 0 but recovery_sleep_days_7d is not, do not "
        "invent HRV recovery narrative.\n"
        "- acute_chronic_ratio null means the 7d vs 28d cardio ratio could not be "
        "computed (often insufficient chronic minutes); do not describe it as high load.\n"
        "- training_load_* are modality-split EXTERNAL training exposure (minutes or US short tons); "
        "they are not HR-derived physiological stress.\n"
        "- effort_unified_index_* is a HEURISTIC single scale (minutes + short tons × a fixed factor); "
        "do not equate it to TRIMP or medical stress.\n"
        "- effort_foster_* uses session/set RPE when logged; NULL components mean RPE was not "
        "captured — do not invent Foster load.\n\n"
        "Write the morning briefing now."
    )


def generate_briefing(
    *,
    user_id: str,
    feature_date: date,
    flags: Sequence[Flag],
    features: Mapping[str, Any],
    llm: LLMClient,
    daily_metrics: Mapping[str, Any] | None = None,
    model: str = DEFAULT_BRIEFING_MODEL,
) -> Briefing:
    """Build the prompt, call the injected ``llm``, and return a :class:`Briefing`.

    Raises:
        ValueError: If the model returns empty text.
    """
    prompt = build_prompt(
        feature_date=feature_date, flags=flags, features=features, daily_metrics=daily_metrics
    )
    note = llm(SYSTEM_GUIDELINES, prompt).strip()
    if not note:
        raise ValueError("LLM returned an empty coaching note")
    logger.info("Generated briefing for %s on %s (%d flags)", user_id, feature_date, len(flags))
    return Briefing(
        user_id=user_id,
        briefing_date=feature_date,
        coaching_note=note,
        flags=[f.code for f in flags],
        features_json={k: _jsonable(v) for k, v in features.items() if v is not None},
        model_used=model,
    )

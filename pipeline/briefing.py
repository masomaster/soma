"""Phase 6 briefing synthesis: turn pre-computed signals into a coaching note.

The LLM **narrates conclusions that were already computed** (flags + features +
optional **stat_signals** z-score block) — it does not reason over raw events
(see ``.cursor/rules/soma.mdc``). The model client is injected as a simple
``Callable`` so this module has no hard dependency on Anthropic and is fully
unit-testable; the Lambda wires in a Haiku-backed client. The returned
:class:`Briefing` maps onto the ``daily_briefings`` table; ``stat_signals`` is
stored inside ``features_json`` for auditability.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pipeline.guidelines import GuidelinesContext, format_guidelines_for_prompt
from pipeline.metrics_summary import format_glance_section
from pipeline.rules import Flag
from pipeline.units import km_to_miles

logger = logging.getLogger(__name__)

# Pinned snapshot ID (see Anthropic model deprecations). Aliases like
# ``claude-haiku-4-5`` also work; avoid retired IDs such as ``claude-3-5-haiku-latest``.
DEFAULT_BRIEFING_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_GUIDELINES = (
    "You are Soma, a concise personal fitness companion for a hobbyist athlete. "
    "You are given PRE-COMPUTED signals (flags) and numeric features. Turn those "
    "signals into a short morning check-in of actionable reminders and gentle "
    "suggestions — not orders or warnings. Do NOT invent data, do NOT contradict "
    "the flags, and do NOT perform your own statistical analysis of raw history — "
    "only explain what you are given. Lead with the highest-priority signal. "
    "TONE: warm, conversational, and low-pressure. Never use commanding or "
    "alarmist phrasing (e.g. you must, critical, urgent, immediately, "
    "non-negotiable, mandatory). Prefer soft suggestions (might consider, worth "
    "noting, one idea for today). This is hobby training, not medical care — do "
    "not imply catastrophe or mandatory action. If SPARSE_RECOVERY_DATA is present, "
    "do not describe sleep debt, sleep quality, or HRV trends. If "
    "recovery_sleep_days_7d is 0, do not describe weekly sleep debt or multi-day "
    "sleep trends (last night in TODAY'S METRICS may still be cited if flags "
    "mention it). If recovery_hrv_days_7d is 0, do not describe HRV trends. If "
    "acute_chronic_ratio is null, note load ratio was not computed (usually little "
    "cardio in the 28-day window) — do not describe it as a spike or elevated "
    "load from ACWR. If overall_readiness_score is null, note readiness could not "
    "be scored from recovery data and focus on training load. When training_load_* "
    "or effort_unified_index_* appear in FEATURES, treat training_load_* as "
    "modality-split external exposure (minutes or US short tons) and "
    "effort_unified_index_* as a heuristic combined trend—not HR TRIMP or "
    "clinical stress. "
    "WORKLOAD_PACE lights: red means overload; yellow underload means room to "
    "build — never call underload 'overloaded'. "
    "STATISTICAL_SIGNALS lists z-score outliers vs the athlete's prior daily baseline "
    "(see baseline_n). Do not contradict listed z-scores or directions; if the "
    "anomalies list is empty, do not invent statistical outliers. "
    "TRENDS lists EWMA drift signals — narrate only if present. "
    "ACTIVE_PATTERNS lists confirmed cross-metric correlations — cite briefly, do not invent new ones. "
    "GOALS_STATUS and TODAYS_FOCUS are pre-computed weekly goal progress — narrate; do not invent counts. "
    "STRENGTH_PROGRESS and TRAINING_PHASE are pre-computed lifting trends and schedule blocks — cite briefly; "
    "do not invent exercise numbers or phase dates. Do NOT list individual key lifts "
    "or top exercises unless a flag explicitly references one. "
    "ATHLETE_JOURNAL lists the athlete's own saved notes — respect them; do not invent journal entries. "
    "PERSONAL GOALS and INJURY HISTORY blocks are athlete-provided context — respect injury constraints "
    "and do not invent injuries or goals beyond what is stated. "
    "A DATA_QUALITY_* flag means a metric looks mis-recorded (e.g. a run's distance); "
    "mention it briefly and neutrally as a 'worth verifying' note — never alarm, and do "
    "not treat the suspect number as real. "
    "FORMAT: write ONLY Markdown bullet points for the coaching body — 3 to 5 "
    "short bullets of concrete actions, suggestions, or reminders for today. "
    "No paragraph prose, no numbered lists, no closing question. Each bullet is "
    "one tight line. An optional single bold lead-in line before the bullets is "
    "OK only when it names the top signal. "
    "Do NOT write your own title, date, or greeting line — a 'Morning Check-In' "
    "header is added for you, so begin directly with the substance. "
    "Keep the whole note under 120 words."
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


def _mileage_check_for_prompt(mileage: Any) -> Any:
    """Re-key a stored (km) ``mileage_check`` into miles so the LLM reports miles.

    Non-mapping values (e.g. ``None``) pass through unchanged; ``change_pct`` is a
    unit-independent ratio and is preserved.
    """
    if not isinstance(mileage, Mapping):
        return mileage
    return {
        "flag": mileage.get("flag"),
        "this_week_miles": km_to_miles(mileage.get("this_week_km")),
        "last_week_miles": km_to_miles(mileage.get("last_week_km")),
        "change_pct": mileage.get("change_pct"),
    }


# A leading heading / "Morning Check-In" line the model may emit despite the
# system guidelines; stripped so the canonical title is never duplicated.
_LEADING_TITLE_RE = re.compile(r"^\s*#{0,6}\s*morning\s+check[- ]?in\b.*$", re.IGNORECASE)
# Sentence boundary: split after ., !, or ? followed by whitespace.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def format_briefing_title(feature_date: date) -> str:
    """Canonical check-in title, e.g. ``Morning Check-In · Thursday, July 2``."""
    return f"Morning Check-In · {feature_date:%A, %B} {feature_date.day}"


def _strip_trailing_question(note: str) -> str:
    """Drop a trailing question/follow-up sentence so briefings end declaratively.

    Belt-and-suspenders alongside the system guideline: the model is told not to
    end with a question, but we also enforce it so a stray "How are you feeling?"
    never ships. Returns the original note if stripping would empty it.
    """
    text = note.rstrip()
    if not text.endswith("?"):
        return note
    paragraphs = re.split(r"\n\n+", text)
    last = paragraphs[-1].rstrip()
    sentences = _SENTENCE_BOUNDARY_RE.split(last)
    while sentences and sentences[-1].rstrip().endswith("?"):
        sentences.pop()
    rebuilt = " ".join(s.strip() for s in sentences if s.strip()).rstrip()
    if rebuilt:
        paragraphs[-1] = rebuilt
    else:
        paragraphs.pop()
    result = "\n\n".join(paragraphs).rstrip()
    return result if result else note


def _prepend_title(note: str, feature_date: date, *, glance_block: str | None = None) -> str:
    """Assemble the final briefing: title, optional glance summary, then prose.

    Any leading heading or "Morning Check-In" line the model emitted is removed
    first so the enforced title is never duplicated. ``glance_block`` (a
    pre-computed Markdown "At a Glance" section) is placed between the title and
    the LLM prose so the reader gets a quick numeric summary before the narrative.
    """
    lines = note.lstrip("\n").split("\n")
    if lines and (lines[0].lstrip().startswith("#") or _LEADING_TITLE_RE.match(lines[0])):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    body = "\n".join(lines).strip()
    parts = [f"# {format_briefing_title(feature_date)}"]
    if glance_block:
        parts.append(glance_block)
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def build_prompt(
    *,
    feature_date: date,
    flags: Sequence[Flag],
    features: Mapping[str, Any],
    daily_metrics: Mapping[str, Any] | None = None,
    stat_signals: Mapping[str, Any] | None = None,
    active_patterns: Sequence[str] | None = None,
    goal_snapshot: Mapping[str, Any] | None = None,
    guidelines: GuidelinesContext | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
    athlete_journal: Sequence[Mapping[str, Any]] | None = None,
    workload_pace: Mapping[str, Any] | None = None,
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
    stat_block = stat_signals if stat_signals is not None else {"anomalies": [], "trends": []}
    stat_blob = json.dumps(stat_block, indent=2, sort_keys=True, default=str)
    trends = stat_block.get("trends") if isinstance(stat_block.get("trends"), list) else []
    patterns_lines = (
        "\n".join(f"- {p}" for p in active_patterns)
        if active_patterns
        else "- None stored for this user."
    )
    stat_preamble = (
        "No statistical outliers vs prior baseline for monitored metrics today "
        "(insufficient history or within normal variation)."
        if not stat_block.get("anomalies")
        else "Z-score outliers (do not recompute; narrate briefly if relevant beside flags)."
    )
    goal_block = ""
    if goal_snapshot:
        gs = goal_snapshot.get("goals_status") or {}
        mc = _mileage_check_for_prompt(goal_snapshot.get("mileage_check"))
        focus = goal_snapshot.get("todays_focus")
        goal_block = (
            f"GOALS_STATUS (pre-computed weekly progress):\n"
            f"{json.dumps(gs, indent=2, sort_keys=True, default=str)}\n\n"
            f"MILEAGE_CHECK (distances in miles):\n"
            f"{json.dumps(mc, indent=2, sort_keys=True, default=str)}\n\n"
            f"TODAYS_FOCUS (deterministic — narrate, do not replan):\n{focus}\n\n"
        )
    guidelines_block = format_guidelines_for_prompt(guidelines)
    strength_block = ""
    if strength_progress:
        compact = {
            k: v
            for k, v in strength_progress.items()
            if k not in ("exercise_series",)
        }
        strength_block = (
            "STRENGTH_PROGRESS (pre-computed lifting trends):\n"
            f"{json.dumps(compact, indent=2, sort_keys=True, default=str)}\n\n"
        )
    phase_block = ""
    if training_phase:
        phase_block = (
            "TRAINING_PHASE (current block schedule — narrate if relevant):\n"
            f"{json.dumps(training_phase, indent=2, sort_keys=True, default=str)}\n\n"
        )
    journal_block = ""
    if athlete_journal:
        journal_block = (
            "ATHLETE_JOURNAL (athlete-saved notes — cite when relevant; do not invent):\n"
            f"{json.dumps(list(athlete_journal), indent=2, sort_keys=True, default=str)}\n\n"
        )
    pace_block = ""
    if workload_pace:
        compact_pace = {
            k: {
                kk: vv
                for kk, vv in (v if isinstance(v, Mapping) else {}).items()
                if kk != "weekly_rollups"
            }
            if isinstance(v, Mapping)
            else v
            for k, v in workload_pace.items()
            if k != "as_of"
        }
        pace_block = (
            "WORKLOAD_PACE (pre-computed green/yellow/red training pace lights — "
            "narrate briefly when yellow or red; do not recompute):\n"
            f"{json.dumps(compact_pace, indent=2, sort_keys=True, default=str)}\n\n"
        )
    return (
        f"{guidelines_block}"
        f"Date: {feature_date.isoformat()}\n\n"
        f"FLAGS (pre-computed, narrate these in priority order):\n{flag_lines}\n\n"
        f"FEATURES (rolling computed metrics):\n{feature_blob}\n\n"
        f"TODAY'S METRICS:\n{metrics_blob}\n\n"
        f"STATISTICAL_SIGNALS (pre-computed; {stat_preamble}):\n{stat_blob}\n\n"
        f"TRENDS (EWMA drift; narrate only if non-empty):\n"
        f"{json.dumps(trends, indent=2, sort_keys=True, default=str)}\n\n"
        f"ACTIVE_PATTERNS (stored correlations; do not invent):\n{patterns_lines}\n\n"
        f"{goal_block}"
        f"{strength_block}"
        f"{phase_block}"
        f"{journal_block}"
        f"{pace_block}"
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
        "- acute_chronic_ratio is the rolling 7d÷28d cardio minutes ratio on "
        "daily_features (distinct from calendar-week ACWR on workload pace lights). "
        "Null means it could not be computed; do not describe it as high load.\n"
        "- training_load_* are modality-split EXTERNAL training exposure (minutes or US short tons); "
        "they are not HR-derived physiological stress.\n"
        "- effort_unified_index_* is a HEURISTIC single scale (minutes + short tons × a fixed factor); "
        "do not equate it to TRIMP or medical stress.\n"
        "- effort_foster_* uses session/set RPE when logged; NULL components mean RPE was not "
        "captured — do not invent Foster load.\n"
        "- All distances are in statute MILES (mi); report running distance in miles, never km.\n\n"
        "Write the morning briefing now as 3–5 Markdown action bullets "
        "(optional one-line bold lead-in). No paragraphs."
    )


def generate_briefing(
    *,
    user_id: str,
    feature_date: date,
    flags: Sequence[Flag],
    features: Mapping[str, Any],
    llm: LLMClient,
    daily_metrics: Mapping[str, Any] | None = None,
    stat_signals: Mapping[str, Any] | None = None,
    active_patterns: Sequence[str] | None = None,
    goal_snapshot: Mapping[str, Any] | None = None,
    guidelines: GuidelinesContext | None = None,
    week_activity: Mapping[str, Any] | None = None,
    run_sessions_7d: int | None = None,
    strength_progress: Mapping[str, Any] | None = None,
    training_phase: Mapping[str, Any] | None = None,
    athlete_journal: Sequence[Mapping[str, Any]] | None = None,
    workload_pace: Mapping[str, Any] | None = None,
    model: str = DEFAULT_BRIEFING_MODEL,
) -> Briefing:
    """Build the prompt, call the injected ``llm``, and return a :class:`Briefing`.

    The returned ``coaching_note`` leads with a deterministic "At a Glance" metrics
    summary (pre-computed here, not by the model), followed by LLM action bullets.

    Raises:
        ValueError: If the model returns empty text.
    """
    stat_block = stat_signals if stat_signals is not None else {"anomalies": [], "trends": []}
    # Glance should not surface key-lift highlights.
    strength_for_glance = None
    if strength_progress:
        strength_for_glance = {
            k: v for k, v in strength_progress.items() if k != "top_exercises"
        }
    prompt = build_prompt(
        feature_date=feature_date,
        flags=flags,
        features=features,
        daily_metrics=daily_metrics,
        stat_signals=stat_block,
        active_patterns=active_patterns,
        goal_snapshot=goal_snapshot,
        guidelines=guidelines,
        strength_progress=strength_for_glance,
        training_phase=training_phase,
        athlete_journal=athlete_journal,
        workload_pace=workload_pace,
    )
    note = llm(SYSTEM_GUIDELINES, prompt).strip()
    if not note:
        raise ValueError("LLM returned an empty coaching note")
    glance_block = format_glance_section(
        features=features,
        daily_metrics=daily_metrics,
        flags=flags,
        goal_snapshot=goal_snapshot,
        week_activity=week_activity,
        run_sessions_7d=run_sessions_7d,
        strength_progress=strength_for_glance,
        training_phase=training_phase,
        workload_pace=workload_pace,
    )
    note = _prepend_title(
        _strip_trailing_question(note), feature_date, glance_block=glance_block
    )
    logger.info("Generated briefing for %s on %s (%d flags)", user_id, feature_date, len(flags))
    features_json = {k: _jsonable(v) for k, v in features.items() if v is not None}
    features_json["stat_signals"] = stat_block
    if active_patterns:
        features_json["active_patterns"] = list(active_patterns)
    if goal_snapshot:
        features_json["goals_status"] = goal_snapshot.get("goals_status")
        features_json["mileage_check"] = goal_snapshot.get("mileage_check")
        features_json["todays_focus"] = goal_snapshot.get("todays_focus")
    if strength_progress:
        features_json["strength_progress"] = {
            k: v for k, v in strength_progress.items() if k != "exercise_series"
        }
    if training_phase:
        features_json["training_phase"] = training_phase
    if athlete_journal:
        features_json["athlete_journal"] = list(athlete_journal)
    if workload_pace:
        features_json["workload_pace"] = {
            k: (
                {kk: vv for kk, vv in v.items() if kk != "weekly_rollups"}
                if isinstance(v, Mapping)
                else v
            )
            for k, v in workload_pace.items()
        }
    return Briefing(
        user_id=user_id,
        briefing_date=feature_date,
        coaching_note=note,
        flags=[f.code for f in flags],
        features_json=features_json,
        model_used=model,
    )

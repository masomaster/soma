# Briefing LLM — known failure modes and mitigations (Phase 6.6)

The daily briefing uses a small model (**Haiku-class**) over a **structured prompt**: pre-computed **flags** + **features** + optional **same-day metrics** + **STATISTICAL_SIGNALS** (z-score outliers when baseline history is sufficient). The model’s job is to **narrate**, not to re-derive physiology. Output tone is **hobbyist-friendly**: neutral reminders and gentle suggestions — not commands, alarms, or clinical urgency. Below are common failure modes and how Soma reduces them.

## Inventing recovery narrative without data

**Symptom:** Sleep debt, “HRV trending down,” or readiness language when biometrics are missing or sparse.

**Mitigations:**

- **Features:** `sleep_debt_7d` is `null` when no sleep observations in the 7-day window; `overall_readiness_score` is `null` when both sleep and HRV observation counts are zero (`pipeline/features.py`).
- **Rules:** `SPARSE_RECOVERY_DATA` info flag when both recovery day counts are zero; `HIGH_SLEEP_DEBT` / `LOW_HRV` are skipped when the corresponding 7-day observation count is explicitly zero (`pipeline/rules.py`).
- **Prompt:** `SYSTEM_GUIDELINES` and the `UNITS / INTERPRETATION` block in `build_prompt` forbid weekly sleep/HRV story when coverage is zero or when `SPARSE_RECOVERY_DATA` is present (`pipeline/briefing.py`).

## Confusing strength tonnage units

**Symptom:** Calling `strength_tonnage_7d` “tonnes” or metric mass.

**Mitigations:**

- Column comment and code: **US short tons** = lb·reps / 2000 (`pipeline/features.py`, migration comment).
- Prompt explicitly states US short tons and warns not to call them metric tonnes without conversion.

## Misreading null ACWR as “high load”

**Symptom:** Load-spike or injury-risk language when `acute_chronic_ratio` is `null` (often little or no cardio in the chronic window).

**Mitigations:**

- Feature computation leaves ACWR `null` when chronic weekly average is zero (`pipeline/features.py`).
- Prompt instructs: null means not computed — do not describe as a spike (`pipeline/briefing.py`).

## Overwriting deterministic severity

**Symptom:** Model dismisses an **alert** flag or contradicts a numeric threshold.

**Mitigations:**

- System prompt: do not contradict flags; lead with the highest-priority signal.
- Staging checklist: compare narrative to flag list (`docs/plans/briefing-staging-inbox-checklist.md`).

## Alarmist or commanding tone

**Symptom:** Briefing reads like a medical warning or order — e.g. “you must,” “critical,” “urgent,” “immediately,” or mandatory injury-risk language when the user is a hobbyist.

**Mitigations:**

- `SYSTEM_GUIDELINES`: hobbyist framing, low-pressure tone, explicit ban on commanding/alarmist phrasing; prefer soft suggestions (`pipeline/briefing.py`).
- Staging checklist: skim for commanding or clinical urgency words that do not match the intended voice.

## Partial recovery coverage (sleep only or HRV only)

**Symptom:** Weekly story for the modality with zero observation days.

**Mitigations:**

- Prompt bullets for `recovery_sleep_days_7d == 0` or `recovery_hrv_days_7d == 0` cases (`build_prompt`).
- Same-day `sleep_hours` in **TODAY'S METRICS** may still support **LOW_SLEEP** when last night is short; that is intentional and distinct from weekly debt.

## Contradicting STATISTICAL_SIGNALS (z-scores)

**Symptom:** Model says HRV is “normal” or invents a different deviation when **STATISTICAL_SIGNALS** lists a z-score outlier (or the reverse: claims a big outlier when the anomalies list is empty).

**Mitigations:**

- `SYSTEM_GUIDELINES`: do not contradict listed z-scores or directions; do not invent outliers when the anomalies array is empty (`pipeline/briefing.py`).
- Treat **baseline_n** as context (“vs ~N prior days”); do not recompute z-scores in prose.

## Email HTML and client quirks

**Symptom:** Markdown raw characters in HTML-only view, or tiny unreadable text.

**Mitigations:**

- Multipart **text + HTML**; HTML uses escaped content and limited Markdown-ish constructs (`pipeline/delivery.py`).
- Staging checklist covers HTML smoke.

## When to escalate

- Repeated hallucination **after** flags/features are correct → tighten `SYSTEM_GUIDELINES`, shorten feature JSON exposed to the model, or add a post-LLM guard (future phase).
- Wrong **numeric** claims that match neither features nor flags → investigate **rules** / **features** bugs first (deterministic layer), not the model.

# Weekly / monthly workload and “stress” indicators

**Purpose:** Ground product decisions for a **week**- and **month**-scale **training load** / **exposure** summary in Soma. The owner wants something as simple as **total exercise time**, or something more sophisticated, framed honestly given current and near-term data.

**Related plan:** [implementation-plan.md](./implementation-plan.md) (cross-cutting slice after Phase 8; surfaced in Phase 9 dashboard).

**Research:** Internal deep-research memo (2026-06) triangulating sports-science literature on internal vs external load, TRIMP/sRPE, ACWR caveats, and resistance-training volume proxies. This file is the **durable repo summary**; treat PubMed / journal links in that memo as the citation layer when you need primary sources.

---

## What Soma can measure today (no new sensors)

| Signal | Source | In `daily_features` (after migration `0003`) |
|--------|--------|----------------------------------------|
| Strength external load | Hevy → `strength_events` (reps × `weight_lbs`, working sets) | `strength_tonnage_7d` + `training_load_strength_short_tons_7d` / `_28d`, `training_load_strength_hard_sets_28d`, `training_load_strength_sessions_28d` |
| Cardio external exposure | Strava summary → `cardio_events` (`duration_min`) | `cardio_minutes_7d` + `training_load_cardio_minutes_7d` / `_28d` |
| Recovery / response | Biometrics → `daily_health_metrics` | Readiness-related features, sleep/HRV coverage flags — **response**, not training dose |
| Unified effort (heuristic) | Derived | `effort_unified_index_7d` / `_28d` |
| Foster-style internal load | `cardio_events.session_rpe`, `strength_events.rpe` | `effort_foster_cardio_au_*`, `effort_foster_strength_au_*`, `effort_foster_au_*` |

**Gaps for “fancy” load:** No **session HR stream** or **power** from Strava summaries → **Banister / Edwards TRIMP** are not implementable at intended fidelity. **RPE after session** is not captured yet → **Foster session load (duration × session RPE)** is a strong **v1** path but needs UX + storage.

---

## Definitions (avoid marketing overclaim)

- **External load:** Time, distance, weight × reps — what the body *did* mechanically.
- **Internal load:** Physiological / perceptual cost (HR-integrated TRIMP, session RPE, etc.).
- **“Stress” in product copy:** Prefer **training exposure** or **load** unless you have HR or validated subjective load; recovery metrics (HRV, RHR, sleep) reflect **response to life + training**, not dose — do not merge into one opaque “stress number” without clear labeling.

---

## Ranked approaches for *this* data profile

1. **Modality-split weekly + rolling ~28d external metrics** (recommended **v0**).  
   Cardio: **minutes** (and later by activity type). Strength: **tonnage / volume load** plus **session and hard-set counts**. Show **trends**, not a single mystery composite. Low implementation cost; aligns with internal/external taxonomy used in load-monitoring literature.

2. **Optional Foster-style load (v1).**  
   **Session RPE (0–10) × duration** gives a portable **internal** load across strength and cardio when HR is missing. Needs consistent post-session input and honest UX about recall bias.

3. **ACWR / spike heuristics on a chosen series (minutes, or arbitrary units after v1).**  
   Soma already computes **acute:chronic ratio on cardio minutes**. Treat as **“recent load vs baseline”**, not individual injury prediction — literature includes methodological critiques (coupling, weak causal claims at individual level). EWMA-style smoothing is a reasonable later enhancement if you keep the framing modest.

---

## Pitfalls (engineering + product)

- **Double-counting:** Same calendar window might get both a Strava “circuit” and Hevy sets — define **dedup or ownership** rules for hybrid work.
- **Adding tonnage to minutes:** Any single **unified index** requires an explicit arbitrary scale; document that it is **heuristic**, not metabolically derived.
- **TRIMP / “sTRIMP” naming:** Peer-reviewed parallel for non-HR internal load is **session RPE × duration**; HR-TRIMP needs HR. Avoid implying full TRIMP without zones/HR.

---

## Implementation phasing (repo)

| Phase | Scope |
|-------|--------|
| **Shipped (repo)** | Migration `0003_training_load_and_effort.sql`: `training_load_*` (7d/28d cardio minutes + strength short tons, 28d hard sets / sessions), `effort_unified_index_*` (heuristic: minutes + short tons × `EFFORT_STRENGTH_SHORT_TON_AS_EQUIV_CARDIO_MINUTES` in `pipeline/features.py`), `effort_foster_*` (Foster AU from `cardio_events.session_rpe` × `duration_min` and strength working-set `rpe` with a minutes-per-set proxy). `pipeline/clients.py` loads **28d** of strength rows for feature computation. Slice A: `weekly_activity_summary` stores **ISO calendar week** (`week_start` = Monday via `iso_week_start`) session counts, running km, and cardio minutes — **not** strength tonnage yet. |
| **Planned (Slice A / Phase 9)** | **Calendar Mon–Sun strength tonnage** in `weekly_activity_summary`: extend `compute_weekly_activity_summary` to sum `reps × weight_lbs` for **working sets** (`set_type = working`, same rule as `pipeline/features.py`) over `[week_start, week_start + 6]`. Persist in **`summary_json`** (e.g. `strength_short_tons`, `strength_hard_sets`, optional `strength_volume_lbs`) — no migration required for v1. Surface in dashboard context + briefing/chat so “how much did I lift **this week** (Mon–Sun)?” does not require text-to-SQL or trailing-7d interpretation. Distinct from `daily_features.training_load_strength_short_tons_7d` (rolling window ending on `feature_date`). |
| **Next (dashboard)** | Phase 9: charts for `training_load_*` and optional `effort_*`; wire calendar-week tonnage into dashboard widgets and bounded chat context. **Shipped:** `pipeline/workload_pace.py` — rolling 7d ACWR / vs prior-7d / vs 4×7d avg for pace **lights** (green = ok including underload; yellow/red = overload only); calendar-week series kept for charts. |
| **Later** | HR/power TRIMP, EWMA chronic smoothing (see [implementation-plan.md § Signal pipeline](./implementation-plan.md#signal-pipeline-where-intelligence-lives) Layer 2), dedup hybrid Strava+Hevy days. |

---

## Open decisions for the product owner

- Is “week” **calendar ISO week**, **rolling 7d**, or **user-configurable** (shift workers)?
- Do you want **one** headline number for marketing, or **always two numbers** (strength vs cardio) for honesty?
- When Apple Health / other streams add **HR**, revisit TRIMP family and **agreement** between objective and subjective load in the briefing narrative.

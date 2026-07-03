# Fitbit sleep score → Soma

## Diagnosis: why Fitbit's Sleep Score never arrived

**Fitbit's proprietary 0–100 "Sleep Score" cannot reach Soma through Apple Health.**

- Soma has **no direct Fitbit adapter**. Fitbit data reaches Soma only through the
  hub chain: **Fitbit → Health Sync → Apple Health (HealthKit) → Health Auto
  Export (HAE) webhook → `pipeline/adapters/apple_health_export.py`** (everything
  lands tagged `source = apple_health_export`).
- **HealthKit has no sleep-score data type.** There is no `HKQuantityType` or
  category for a sleep score, so no bridge/sync app can write Fitbit's score into
  Apple Health, and HAE therefore cannot forward it. (Apple's own iOS 26 sleep
  score has **no public read/write API** either — confirmed via Apple Developer
  Technical Support.) Fitbit's score also is **not** in the Fitbit Web API; it
  exists only inside Fitbit's manual *Export Your Account Archive* download.
- Historically the adapter produced **only** `sleep_hours` (from HAE
  `sleep_analysis`/`sleepanalysis` → `totalSleep`). The canonical columns
  `sleep_score`, `sleep_deep_hrs`, `sleep_rem_hrs` already existed in
  `daily_health_metrics` and in every allow-list, but **nothing ever wrote them**.

### Conclusion / decision

We will **not** chase Fitbit's number (it is unreachable without a fragile manual
CSV import). Instead Soma:

1. **Ingests the sleep *stages* that HealthKit *does* carry** — deep / REM
   durations flow through the existing HAE path once a Fitbit→Apple Health bridge
   syncs them (see setup below).
2. **Computes its own `sleep_score` (0–100)** as a deterministic, transparent
   **pre-computed conclusion** in the feature/rollup layer. Per the architecture
   rule "the LLM explains pre-computed conclusions", the adapter only normalizes
   raw stage data; the score is derived in `pipeline/sleep_score.py` and written
   to `daily_health_metrics.sleep_score` during the daily rollup.

The tracked score is therefore **Soma-computed, not Fitbit's** — an intentional,
reproducible metric rather than a black box.

## What changed

| Area | Change |
|------|--------|
| `pipeline/adapters/apple_health_export.py` | `_HAE_NAME_TO_CANONICAL` + `sleep_analysis` handling now emit `sleep_deep_hrs` / `sleep_rem_hrs` from per-stage durations (`deep`/`rem` inside a sleep row, or standalone `sleep_deep`/`sleep_rem` metrics), unit-aware (hours/minutes/seconds → hours). Handles the HAE aggregated-row shape and the Soma daily-envelope shape. |
| `pipeline/sleep_score.py` (new) | `compute_sleep_score(...)` — the native 0–100 formula; `trailing_baseline(...)` for personal HRV / resting-HR baselines. Stdlib-only, deterministic. |
| `pipeline/features.py` | `rollup_daily_health_metrics(...)` computes `sleep_score` from the day's signals when a source didn't supply one. New optional `sleep_need_hours` / `hrv_baseline` / `resting_hr_baseline` kwargs. |
| `pipeline/orchestration.py` | The daily pipeline derives trailing HRV / resting-HR baselines from the metrics window (loaded once, shared with feature computation) and passes them into the rollup. |

No schema migration was required: `sleep_score`, `sleep_deep_hrs`, and
`sleep_rem_hrs` already exist as columns (`schema/migrations/0001_initial.sql`)
and in `DAILY_HEALTH_METRIC_COLUMNS`, `persistence.py`, and the adapter's
`_MAX_PER_DAY` dedup set.

## The sleep-score formula

A weighted blend of up to **five** components, each scored in `[0, 1]`:

| Component | Weight | Signal | Direction |
|-----------|-------:|--------|-----------|
| duration   | 0.30 | `sleep_hours` vs personal need (default 8h) | closeness to need |
| stages     | 0.30 | deep & REM fraction of total vs optima (deep ≈18%, REM ≈22%) | closeness to optimum |
| hrv        | 0.15 | `hrv_rmssd` vs personal baseline | higher is better |
| resting_hr | 0.15 | `resting_hr` vs personal baseline | lower is better |
| awake      | 0.10 | wakefulness / interruptions fraction | less is better |

**Missing-input behavior (graceful degradation):** only components whose inputs
are present contribute; the remaining weights are **renormalized** over the
available components and the result is clamped to `0–100`. So a bare
duration-only day still scores. The HRV and resting-HR components require a
personal **baseline** (trailing 28-day mean); without one they simply don't
contribute — the score is never guessed from an absolute HR/HRV number. If there
is **no sleep duration at all**, no score is produced (`NULL`). Baselines near
`0.75` leave headroom to reward better-than-usual nights.

These constants are heuristics, not a validated clinical model; tune them in
`pipeline/sleep_score.py` as personal data accrues.

## Operator setup — get sleep stages flowing

Because HealthKit has no sleep-score type, you capture **stages** and let Soma
compute the score:

1. **Fitbit → Apple Health bridge (iOS):** install a bridge app that syncs Fitbit
   **sleep stages** into Apple Health — e.g. **SyncFit**, **myFitnessSync**, or
   **Sync Solver for Fitbit**. Enable syncing of **sleep** (stages: deep / REM /
   light / awake), not just a single "asleep" block.
2. **Health Auto Export:** keep (or add) the **metrics** automation POSTing to your
   `AppleHealthIngestUrl` (see [apple-health-export.md](./apple-health-export.md)).
   HAE will forward the `sleep_analysis` block — now including stage durations —
   to Soma, which maps them to `sleep_deep_hrs` / `sleep_rem_hrs`.
3. **Verify:** `python scripts/smoke_apple_health.py normalize <export.json>` — you
   should see `sleep_deep_hrs` / `sleep_rem_hrs` rows. After the daily rollup,
   `daily_health_metrics.sleep_score` will be populated (Soma-computed).

> The number you track is **Soma's** computed sleep score, derived from the stages
> and recovery signals that actually reach the pipeline — not Fitbit's private
> score, which no API or Apple Health bridge exposes.

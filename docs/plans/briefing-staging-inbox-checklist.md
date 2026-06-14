# Staging inbox checklist — daily briefing email (Phase 6.6)

Use after the first **SES** smoke send from staging (`ENV=staging`). Goal: confirm deterministic signals, copy, and rendering match intent before widening recipients.

## Subject and envelope

- [ ] Subject is prefixed with `[STAGING]` (not in prod).
- [ ] From address matches verified SES identity.
- [ ] Message is **multipart**: plain text + HTML (view “original” or “all headers” in your client if needed).

## Plain-text part

- [ ] Coaching note is readable without HTML (no reliance on formatting alone).
- [ ] No obvious placeholder or truncated mid-sentence text.

## HTML part

- [ ] Document has `lang="en"` and readable system font stack.
- [ ] **Soma** header appears once; coaching body follows.
- [ ] Bold segments from `**...**` render as emphasis in HTML.
- [ ] Lists and short paragraphs are not run together as a single wall of text.
- [ ] If `BRIEFING_EMAIL_DASHBOARD_URL` is set: footer link works and visible URL matches (no `javascript:` or odd schemes).

## Content vs data (spot-check against Supabase or logs)

- [ ] **Strength volume:** if `strength_tonnage_7d` appears, copy treats it as **US short tons** (not “tonnes” unless converted).
- [ ] **SPARSE_RECOVERY_DATA** or zero `recovery_*_days_7d`: no invented weekly sleep debt or HRV story.
- [ ] **`acute_chronic_ratio` null:** no “ACWR spike” narrative; model should treat ratio as not computed.
- [ ] **`overall_readiness_score` null:** copy says readiness was not scored (or equivalent), not a fake numeric score.
- [ ] Flags in the email narrative align with **rules** output for that run (severity order: worst first).
- [ ] **STATISTICAL_SIGNALS (Phase 8):** if the prompt block lists z-score outliers, the note does not contradict listed **z_score** / **direction**; if the anomalies array is empty, the model does not invent statistical outliers (see [briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md) § Contradicting STATISTICAL_SIGNALS).
- [ ] **`anomaly_events`:** count of `anomaly_type = 'statistical'` rows for that user/day matches expectations (**often zero** when z-scores do not fire; the run still **deletes** prior statistical rows for that day, then may insert none). Optional SQL in Supabase.

## Preconditions (Hevy + Apple on staging)

The briefing Lambda **reads Postgres only** — it does not call Hevy or Apple.

- [ ] **Apple — today:** **`biometrics`** has rows for the run date so the rollup can build **`daily_health_metrics`** for **today** (enough for same-day metrics + many rules).
- [ ] **Apple — history / z-scores:** Older **`daily_health_metrics`** accumulate as the pipeline runs. **Fewer than 14 prior calendar days** with non-null values for a metric ⇒ **no z-score flags** (`stat_signals.anomalies` usually empty; **no** new statistical **`anomaly_events`**). **Normal on a cold start** — not a blocker for an SES smoke.
- [ ] **Apple — history / rolling windows:** **7d/28d** features stay **partial** until windows fill; expect **`SPARSE_RECOVERY_DATA`** and nulls — no invented weekly sleep/HRV story ([briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md)).
- [ ] **Hevy:** **`strength_events`** populated for your user (e.g. **`scripts/smoke_hevy.py db-upsert`**, historical backfill, or scheduled ingest once wired — see [implementation-plan.md](./implementation-plan.md) § *Integration slices*).

## Operational

- [ ] `daily_briefings` row exists for the run date with `model_used` and `flags` JSON matching expectations; **`features_json.stat_signals`** is present (may show empty `anomalies` if baseline history is short).
- [ ] CloudWatch shows no unexpected errors for that invocation; per-user failures are isolated in the handler summary.

## Sign-off

| Date | Reviewer | Result (pass / issues) |
|------|------------|-------------------------|
|      |            |                         |

When prod goes live, repeat without `[STAGING]` and with prod SES identity.

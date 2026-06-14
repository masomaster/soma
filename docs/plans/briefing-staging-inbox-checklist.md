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

## Operational

- [ ] `daily_briefings` row exists for the run date with `model_used` and `flags` JSON matching expectations.
- [ ] CloudWatch shows no unexpected errors for that invocation; per-user failures are isolated in the handler summary.

## Sign-off

| Date | Reviewer | Result (pass / issues) |
|------|------------|-------------------------|
|      |            |                         |

When prod goes live, repeat without `[STAGING]` and with prod SES identity.

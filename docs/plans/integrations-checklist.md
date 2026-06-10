# Integrations checklist — confirm with product owner

This list is derived from [project-overview.md](./project-overview.md) and the README. **Status is “planned”** until you tick each row.

Use it to confirm scope before building adapters. **Reply in-repo** (or issue) with edits: add/remove sources, change priority.

| # | Service | Data you care about | Integration style (typical) | Priority / notes |
|---|---------|----------------------|----------------------------|------------------|
| 1 | **Hevy** | Lifting — sets, reps, weight, RPE | REST API (API key header) | High — primary strength source |
| 2 | **Strava** | Runs/rides — GPS, HR, pace, elevation | OAuth2 + REST | High |
| 3 | **Apple Health (export)** | Steps, HRV, sleep, VO2, resting HR | Third-party app (e.g. Health Auto Export) → **webhook** to your HTTP endpoint | High — often biometric hub |
| 4 | **Google Health / Fit** | Sleep, HR, HRV, weight (Fitbit migration path) | Google APIs + OAuth2 | Medium — align with Fitbit sunset / Google Health roadmap |
| 5 | **Renpho** | Weight, body fat, muscle mass | Unofficial/community APIs (e.g. PyPI clients) | Medium |
| 6 | **iCloud Calendar** | Busy/free blocks for coaching context | CalDAV + app-specific password | Medium — read-only polling |
| 7 | **Anthropic** | Daily briefing + weekly analysis | REST API (API key) | High — not a “health” source but core pipeline |
| 8 | **AWS** | S3 raw, Lambda, EventBridge, SES, SSM, Secrets Manager | SDK + IAM | High — infrastructure |
| 9 | **Supabase** | Postgres + Auth + generated REST | Dashboard, CLI, `rest/v1`, client libs | High |

## Explicitly deprioritized or one-off (per overview)

| Service | Note |
|---------|------|
| **Nike Run Club** | Fragile; **one-time historical export** only if needed; Apple Health / Strava carry ongoing runs. |
| **Fitbit legacy API** | Sunsetting — prefer **Google Health** path rather than new Fitbit work. |

## Not vendor APIs but part of “integration” work

| Piece | Purpose |
|-------|--------|
| **Supabase PostgREST** | Auto CRUD-ish HTTP API over your tables — map after migrations. |
| **Email (SES)** | Outbound briefing — tested from staging/prod AWS, not Bruno unless you add raw SMTP/API tests. |

---

**Your confirmation:** Edit this file (or list deltas in chat) with ✅ / ❌ per row, any renames (e.g. different export app than Health Auto Export), and **order of implementation** if it differs from the table.

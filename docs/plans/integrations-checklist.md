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

## Phase 1 — payload capture (in progress)

Redacted samples and shape tests live under `tests/fixtures/` (see `tests/fixtures/README.md`). Replace fixtures with your own captures after hitting the real APIs; never commit secrets.

| Source | API / path | Pagination / units | Dedup / keys (proposed) |
|--------|------------|--------------------|-------------------------|
| **Hevy** | `GET /v1/workouts` — `page`, `pageSize` (max 10), response `page`, `page_count`, `workouts` | Timestamps ISO 8601; set weights in **kg** (`weight_kg`) — convert to `strength_events.weight_lbs` in ETL | `source_id`: `hevy:{workout_id}:{exercise_index}:{set_index}` — sets expose `index` only (no stable set UUID in list payload); revisit if single-workout endpoint adds ids |
| **Apple Health export** (webhook / daily rollup) | App-specific JSON — normalize to `biometrics` rows | One row per `(event_date, metric)`; values must use [canonical metric names](../../schema/soma-planned-schema.sql) | DB `UNIQUE (user_id, source, event_date, metric)` per planned schema |

**Your confirmation:** Edit this file (or list deltas in chat) with ✅ / ❌ per row, any renames (e.g. different export app than Health Auto Export), and **order of implementation** if it differs from the table.

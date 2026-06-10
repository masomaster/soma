# Integrations checklist — confirm with product owner

This list is derived from [project-overview.md](./project-overview.md) and the README. **Scope:** ✅ **confirmed** — proceed with Hevy + Apple Health export path as first strength + biometrics sources unless priorities change.

Use it to confirm scope before building adapters. For future changes: edit this file (or an issue) with deltas.

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
|-------|---------|
| **Supabase PostgREST** | Auto CRUD-ish HTTP API over your tables — map after migrations. |
| **Email (SES)** | Outbound briefing — tested from staging/prod AWS, not Bruno unless you add raw SMTP/API tests. |

---

## Phase 1 — payload capture (**complete**)

**Closed 2026-06:** Live `GET https://api.hevyapp.com/v1/workouts` exercised with real credentials; wire format matches expectations for migrations + ETL. Redacted samples and shape tests live under `tests/fixtures/` (see `tests/fixtures/README.md`). Never commit secrets or raw personal captures—trim fixtures to synthetic IDs and placeholder titles when refreshing.

| Source | API / path | Pagination / units | Dedup / keys (proposed) |
|--------|------------|--------------------|-------------------------|
| **Hevy** | `GET /v1/workouts` — `page`, `pageSize` (max **10**), response `page`, `page_count`, `workouts` | Timestamps ISO 8601 (`start_time`/`end_time` often `+00:00`; `created_at`/`updated_at` often `Z` with ms). Weights in **kg** (`weight_kg`, nullable for bodyweight); `description`/`notes` may be `""`. Exercises include **`superset_id`** (nullable int; groups supersets). Walk **`page` … `page_count`** until all workouts fetched. | `source_id`: `hevy:{workout_id}:{exercise_index}:{set_index}` — use exercise **`index`**, not title (same title can repeat in one workout). Sets expose `index` only in list payload. **DB:** `strength_events.source_id` is **`NOT NULL`** — adapters must always emit the dedup string before insert. |
| **Apple Health export** (webhook / daily rollup) | App-specific JSON — normalize to `biometrics` rows | One row per `(event_date, metric)`; values must use [canonical metric names](../../schema/soma-planned-schema.sql) | DB `UNIQUE (user_id, source, event_date, metric)` per planned schema |

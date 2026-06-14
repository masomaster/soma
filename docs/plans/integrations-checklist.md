# Integrations checklist — confirm with product owner

This list is derived from [project-overview.md](./project-overview.md) and the README. **Scope:** ✅ **confirmed** — proceed with Hevy + Apple Health export path as first strength + biometrics sources unless priorities change.

**Phase 7 focus (2026-06):** **Strava is paused** — no Strava (athlete) subscription, so live API / OAuth / pipeline wiring for Strava is deferred; the **in-repo adapter + offline tests** stay for when this unpauses. **Apple Health export (webhook → biometrics)** is the **active** integration track (build with another agent/session as needed).

Use it to confirm scope before building adapters. For future changes: edit this file (or an issue) with deltas.

| # | Service | Data you care about | Integration style (typical) | Priority / notes |
|---|---------|----------------------|----------------------------|------------------|
| 1 | **Hevy** | Lifting — sets, reps, weight, RPE | REST API (API key header) | High — primary strength source |
| 2 | **Strava** | Runs/rides — GPS, HR, pace, elevation | OAuth2 + REST | **Paused** — Standard Tier needs an **active Strava subscription**; repo has adapter + fixtures only until unpaused (§ Strava API access) |
| 3 | **Apple Health (export)** | Steps, HRV, sleep, VO2, resting HR | Third-party app (e.g. Health Auto Export) → **webhook** to your HTTP endpoint | High — **active Phase 7 track** (biometric hub); webhook + normalize to `biometrics` |
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

### Strava API access (subscription / tiers)

Strava’s **Standard developer tier** (self-serve apps, including personal tools) is subject to **subscription requirements** published on their developer site: you generally need an **active Strava (athlete) subscription** to use the REST API as a Standard Tier developer—**not** a separate “API-only” product on top of that for typical hobby use. **Extended Access Tier** (large / reviewed apps) has different rules.

Implications for Soma:

- **Paused (operator choice, 2026-06):** no Strava subscription → treat **live Strava** as out of scope until you subscribe or take another access path; keep using **fixtures + offline tests** for regression. **Apple Health export** is the active build track instead.
- **Adapter + tests in repo:** still valid whenever you *do* have a token (paid month for validation, team member with a subscription, etc.); offline tests use fixtures only.
- **Product sequencing:** treat **Apple Health export / other cardio sources** as the unblock for “cardio in the DB” if Strava stays off the table.

Official context (read the current pages; policy dates and details change): [Strava API FAQ](https://communityhub.strava.com/developers-knowledge-base-14/strava-api-faq-12906), [API policy](https://www.strava.com/legal/api_policy), [Developer program updates](https://communityhub.strava.com/insider-journal-9/an-update-to-our-developer-program-13428).

---

## Phase 1 — payload capture (**complete**)

**Closed 2026-06:** Live `GET https://api.hevyapp.com/v1/workouts` exercised with real credentials; wire format matches expectations for migrations + ETL. Redacted samples and shape tests live under `tests/fixtures/` (see `tests/fixtures/README.md`). Never commit secrets or raw personal captures—trim fixtures to synthetic IDs and placeholder titles when refreshing.

| Source | API / path | Pagination / units | Dedup / keys (proposed) |
|--------|------------|--------------------|-------------------------|
| **Hevy** | `GET /v1/workouts` — `page`, `pageSize` (max **10**), response `page`, `page_count`, `workouts` | Timestamps ISO 8601 (`start_time`/`end_time` often `+00:00`; `created_at`/`updated_at` often `Z` with ms). Weights in **kg** (`weight_kg`, nullable for bodyweight); `description`/`notes` may be `""`. Exercises include **`superset_id`** (nullable int; groups supersets). Walk **`page` … `page_count`** until all workouts fetched. | `source_id`: `hevy:{workout_id}:{exercise_index}:{set_index}` — use exercise **`index`**, not title (same title can repeat in one workout). Sets expose `index` only in list payload. **DB:** `strength_events.source_id` is **`NOT NULL`** — adapters must always emit the dedup string before insert. |
| **Strava** | `GET https://www.strava.com/api/v3/athlete/activities` — `page`, `per_page` (max **200**); response is a **JSON array** of summary activities (not wrapped in an object) | `distance` in **meters**; `moving_time` / `elapsed_time` in **seconds**; `total_elevation_gain` in meters; `type` is activity type string (e.g. `Run`, `Ride`); `start_date_local` preferred for calendar **`event_date`**; optional `average_heartrate` / `max_heartrate`; `kilojoules` or `calories` when present. Paginate until a page shorter than `per_page` or empty. Use **`before` / `after`** epoch filters for incremental sync. Respect [rate limits](https://developers.strava.com/docs/rate-limits/). | `source_id`: `strava:{activity_id}` — one **`cardio_events`** row per activity from the list endpoint (detail streams are a later slice). **`source`** column = `strava`. |
| **Apple Health export** (webhook / daily rollup) | App-specific JSON — normalize to `biometrics` rows | One row per `(event_date, metric)`; values must use [canonical metric names](../../schema/soma-planned-schema.sql) | DB `UNIQUE (user_id, source, event_date, metric)` per planned schema |

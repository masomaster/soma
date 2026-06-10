# SQL schema (planned)

This folder documents the **planned** relational schema for Soma. Nothing is applied to your Supabase project until you run migrations from `schema/migrations/` — see [implementation plan Phase 2](../plans/implementation-plan.md) (**Phase 1** API/fixture validation is complete; next step is Phase 2 migrations + RLS).

## Files

| File | Purpose |
|------|--------|
| [schema-diagram.md](./schema-diagram.md) | **Human-readable** entity–relationship view (Mermaid). Start here for a picture of the model. |
| [`../../schema/soma-planned-schema.sql`](../../schema/soma-planned-schema.sql) | **Full DDL** (CREATE TABLE, RLS policies, indexes) — canonical text for diffing and reviews. |

## Tables (quick list)

| Table | Role |
|-------|------|
| `user_settings` | Per-user email, timezone, preferred briefing time (FK → `auth.users`). |
| `strength_events` | One row per strength set; service-agnostic (`source`, `source_id`). |
| `cardio_events` | One row per cardio session. |
| `biometrics` | EAV ingestion layer (metric name + value + day). |
| `daily_health_metrics` | Wide daily row for analysis, rules, anomalies. |
| `daily_features` | Derived training load / readiness features. |
| `interventions` | Supplements, injuries, travel, etc. |
| `daily_briefings` | Persisted flags + LLM output + snapshots. |
| `anomaly_events` | Statistical / LLM anomaly log. |

All domain tables include `user_id` → `auth.users(id)` and are covered by RLS policies in the planned DDL. **Batch jobs using the service role** must still scope by `user_id` explicitly (see [local-dev-and-tooling.md](../plans/local-dev-and-tooling.md)).

## Supabase auto-generated API

After tables exist in a Supabase project, **PostgREST** exposes REST (and the client libraries use it). You will map:

- **Bruno** collections → vendor HTTP APIs (Hevy, Strava, …).
- **App / scripts** → Supabase `https://<project>.supabase.co/rest/v1/<table>` (or supabase-js) with the correct **anon / user JWT** so RLS applies.

Planning detail: [local-dev-and-tooling.md](../plans/local-dev-and-tooling.md) § Supabase REST.

## Keeping docs in sync

1. **Phase 1:** ✅ validate vendor APIs and fixtures; revise `soma-planned-schema.sql` when real payloads imply DDL changes (optional before first migration).  
2. **Phase 2:** add **numbered migrations** under `schema/migrations/` and apply to Supabase.  
3. Either regenerate this overview from migrations or keep `soma-planned-schema.sql` aligned when the model changes.

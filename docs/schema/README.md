# SQL schema (planned)

This folder documents the relational schema for Soma. **Applied state** is defined by numbered files under `schema/migrations/` (see [`0001_initial.sql`](../../schema/migrations/0001_initial.sql)). [implementation plan](../plans/implementation-plan.md) Phase 2 adds RLS + access docs — apply migrations to staging first, then [db access patterns](../plans/db-access-patterns.md).

## Files

| File | Purpose |
|------|--------|
| [schema-diagram.md](./schema-diagram.md) | **Human-readable** entity–relationship view (Mermaid). Start here for a picture of the model. |
| [`../../schema/soma-planned-schema.sql`](../../schema/soma-planned-schema.sql) | **Planned DDL** for diffing and reviews — keep aligned with migrations. |
| [`../../schema/migrations/0002_daily_features_recovery_counts.sql`](../../schema/migrations/0002_daily_features_recovery_counts.sql) | **Second migration** — recovery observation counts + `strength_tonnage_7d` column comment. |

## Tables (quick list)

| Table | Role |
|-------|------|
| `user_settings` | Per-user email, timezone, preferred briefing time (FK → `auth.users`). |
| `strength_events` | One row per strength set; service-agnostic (`source`, `source_id`); Hevy **`superset_id`** in migration. |
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

1. **Phase 1:** ✅ validate vendor APIs and fixtures.  
2. **Phase 2:** ✅ `0001_initial.sql` + [db access patterns](../plans/db-access-patterns.md); apply to **your** Supabase staging/prod projects.  
3. When the model changes, add `0002_*.sql` (etc.) and update `soma-planned-schema.sql` / this README as needed.

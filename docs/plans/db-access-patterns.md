# Database access patterns (Supabase Postgres)

This document records **who** connects to Soma’s domain tables, **which credential** they use, and how that interacts with **RLS**. It complements [implementation-plan.md](./implementation-plan.md) Phase 2 and [local-dev-and-tooling.md](./local-dev-and-tooling.md).

## Roles and keys

| Client | Typical key / session | RLS applied? | Responsibility |
|--------|----------------------|--------------|----------------|
| **End-user app** (future Streamlit / Next.js) | Publishable **anon** key + **`Authorization: Bearer <user JWT>`** | **Yes** | Only rows for `auth.uid()` match policies. |
| **ETL / Lambdas / batch jobs** | **`service_role`** secret key (server only) | **No** (bypasses RLS) | **Must** filter and write with the correct `user_id` in application code. A bug can corrupt or leak cross-tenant data. |
| **Interactive SQL** (Dashboard SQL editor, `psql` as `postgres`) | Database superuser / owner | **No** | Use only for migrations and controlled support; never from app code. |

**Decision (Soma):** pipeline jobs use **`service_role`** (or direct Postgres with a role that bypasses RLS) plus **explicit `user_id`** on every insert/update scoped to the job’s tenant. RLS remains the safety net for **user JWT** paths (REST and clients using the user’s session).

Inserts into **`strength_events`** and **`cardio_events`** must always set **`source_id`** (both columns are **`NOT NULL`** in `0001_initial.sql` so `UNIQUE (user_id, source_id)` dedupes reliably).

## PostgREST (REST)

Base URL: `https://<project-ref>.supabase.co/rest/v1/<table>`

- Send **`apikey`**: anon or service key depending on caller.
- For user-scoped access, send **`Authorization: Bearer <user_access_token>`** from Supabase Auth. PostgREST runs queries as the **`authenticated`** role; table **GRANT**s in `0001_initial.sql` allow DML where RLS permits.

Do **not** ship the **service_role** key to browsers or mobile apps.

## Applying migrations

1. **Staging first:** run the numbered files in `schema/migrations/` against the **staging** Supabase project (SQL Editor paste, `psql` with the pooler URL, or Supabase CLI linked to that project).
2. **Smoke-check RLS** on staging (see below).
3. **Production:** apply the **same ordered migration set** after staging is verified; use a protected process (manual runbook, release checklist, or CI gated on environment approval) so prod is never overwritten accidentally.

Promotion is **not** automatic from this repo alone; it is an operator/CI choice documented in your runbook.

## Verifying RLS (two users)

Goal: **User B’s JWT must see zero rows** in another tenant’s data.

**Setup (staging):**

1. Create two auth users (Dashboard → Authentication, or Auth API).
2. As **postgres** / SQL editor (bypass RLS), insert one row into e.g. `public.strength_events` with `user_id = <user A uuid>` (and valid `event_date`, `source`, etc.).
3. Obtain a **session JWT** for User A and User B (sign-in as each user).

**Check:**

- Call `GET /rest/v1/strength_events` with **User A** anon key + User A JWT → should return User A’s rows.
- Same endpoint with **User B** JWT → should return **no** rows for User A’s data (empty array or filtered set containing only B’s own rows if any).

If User B ever sees User A’s rows, **stop** and fix policies or grants before prod.

## Related files

- Canonical DDL for review: [`schema/migrations/0001_initial.sql`](../../schema/migrations/0001_initial.sql) (applied state) and [`schema/soma-planned-schema.sql`](../../schema/soma-planned-schema.sql) (planning alignment).
- Contract tests: `tests/test_migration_rls_contract.py` (asserts migration text enables RLS + `auth.uid()` policies on all domain tables).

# Local development & tooling (no Docker required)

## Why Docker appeared in earlier docs

The original plan assumed **Docker** for two things:

1. **Postgres** on `localhost` mirroring Supabase, and  
2. **LocalStack** to mock S3 / SSM / SES without touching AWS.

That pattern is common for CI and full offline parity, but it **adds moving parts** (images, volumes, port conflicts, LocalStack quirks). If you prefer to avoid Docker entirely, the project can still be viable.

## Recommended approach without Docker

| Goal | Approach |
|------|----------|
| **Validate vendor APIs** | **[Bruno](https://www.usebruno.com/)** collections under **`.bruno/`** (see [.bruno/README.md](../../.bruno/README.md)). Store secrets in Bruno secrets / local env — never commit tokens. |
| **Validate SQL / RLS** | Use a **Supabase-hosted** project (staging or a dedicated “dev” project). Apply migrations via **Supabase CLI** (`supabase db push`) or Dashboard SQL editor. Use **SQL Editor** or `psql` with the **connection string** from the dashboard (no local Postgres container). |
| **Optional native Postgres** | If you ever want a DB without Supabase: install Postgres via **Homebrew** (`brew install postgresql@16`) — still no Docker. You would lose `auth.users` / RLS helpers unless you use **Supabase CLI linked project** or stub auth. Prefer Supabase for schema truth. |
| **AWS (S3, SES, Lambda)** | Develop against **real staging AWS** with low-volume data, or add LocalStack **only if** you later need offline S3 — that would reintroduce Docker for LocalStack unless you run it elsewhere. |

**Product owner preference (recorded):** avoid Docker for local dev; use Bruno + Supabase (hosted) for API and schema work.

## Bruno (`.bruno/`) & Makefile

- One **collection per vendor** (or per API surface) keeps environments clear: e.g. `hevy`, `strava`, `anthropic`, `supabase-rest`.  
- Use **environments** for `{{baseUrl}}`, `{{api_key}}`, OAuth token vars.  
- For **OAuth** flows (Strava, Google): Bruno can hold the **token refresh** requests; initial authorization may still need a browser once.
- **`Makefile`:** optional shortcuts (`make install`, `make test`); equivalent shell commands are in [README.md](../../README.md) § Development — delete the Makefile if you do not want it.

## Supabase auto-generated REST API

Supabase exposes your tables over **PostgREST** at:

`https://<project-ref>.supabase.co/rest/v1/<table_name>`

Headers typically include:

- `apikey: <anon-or-service-role-key>`  
- `Authorization: Bearer <user-jwt>` for **RLS-enforced** access as that user, or service role for **admin** scripts (use only in trusted automation — bypasses RLS).

**Later mapping (implementation phase):**

| Concern | Where it lives |
|---------|----------------|
| Vendor APIs | Bruno + Python adapters in `pipeline/` |
| Your canonical data | Postgres tables (migrations) |
| Client / tools querying your DB | `rest/v1/...` + RLS, or `supabase-js` |

Document **which operations** are allowed through REST (read-only explorer vs writes from app). For **ETL Lambdas**, you will likely use **service role** + explicit `user_id` in code, not the anon key from a phone app.

## Python scripts without containers

Run exploration scripts on the host with a **venv** (`python -m venv .venv`). No Docker required. Point `DATABASE_URL` or Supabase client env vars at your **dev** project.

## Related docs

- [integrations-checklist.md](./integrations-checklist.md) — services to integrate (confirm with you).  
- [implementation-plan.md](./implementation-plan.md) — Phase 0 updated for non-Docker workflow.  
- [../schema/README.md](../schema/README.md) — schema overview + link to full SQL.

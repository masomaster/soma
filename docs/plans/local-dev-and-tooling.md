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

## Phase 3 smoke (Hevy — live, raw to disk, DB upsert)

Use [`scripts/smoke_hevy.py`](../../scripts/smoke_hevy.py) after `pip install -e ".[dev]"` (includes **`python-dotenv`** so a root **`.env`** is loaded automatically).

| Command | What it proves |
|---------|----------------|
| `python scripts/smoke_hevy.py live` | Live Hevy `GET /v1/workouts` page 1 + normalization (no S3, no DB). |
| `python scripts/smoke_hevy.py raw-disk` | Same fetch, plus **raw JSON** written under `SOMA_RAW_LOCAL_DIR` (default `tmp/soma_raw/`) using the same **key layout** as production S3 (`raw/{user_id}/hevy/...`). |
| `python scripts/smoke_hevy.py db-upsert` | Page 1 fetch + normalize + **`upsert_strength_events`** against Postgres. |

**Environment variables** (copy from [`.env.example`](../../.env.example)):

1. **`HEVY_API_KEY`** — Hevy Pro API key (same as Bruno).  
2. **`SOMA_USER_ID`** — UUID of a real **`auth.users`** row in **staging** (the FK on `strength_events.user_id` requires it). Use Dashboard → Authentication, or create a test user and copy its UUID.  
3. **`SOMA_RAW_LOCAL_DIR`** (optional) — directory root for `raw-disk` (default `tmp/soma_raw`; `tmp/` is gitignored).  
4. **`SOMA_DATABASE_URL`** (for `db-upsert` only) — Supabase **Postgres URI** from the project **Connect** button (Dashboard). Prefer **Shared pooler → Session mode** (`aws-<region>.pooler.supabase.com:5432`, user like `postgres.<project-ref>`) for laptops and IPv4-only networks. The **direct** URI (`db.<project-ref>.supabase.co:5432`) is **IPv6-only** unless you add the paid [IPv4 add-on](https://supabase.com/docs/guides/platform/ipv4-address); on many home/office networks you will see DNS errors such as `could not translate host name` or IPv6 “no route to host”. See [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres). Add `?sslmode=require` if the dashboard string omits it and TLS fails. This URI uses the **database password**, not the anon JWT — it bypasses RLS like other privileged DB sessions. Never commit it.

**Before `db-upsert`:** Supabase **never** receives `schema/migrations/0001_initial.sql` from Git by itself — that file exists **only in this repo** until you apply it. In the Supabase **Dashboard** for your project, open **SQL Editor** (left nav), paste the **full contents** of [`schema/migrations/0001_initial.sql`](../../schema/migrations/0001_initial.sql) from your local clone (or GitHub), click **Run**, and fix any errors in the output. Then confirm `public.strength_events` exists (e.g. `select to_regclass('public.strength_events');`). No branching required for this flow.

### `db-upsert`: “could not translate host name” / cannot reach `db.*.supabase.co`

Per Supabase: **`db.<project-ref>.supabase.co`** (direct, port **5432**) resolves for **IPv6** traffic. Many local networks are **IPv4-only** or resolve IPv6 poorly, which produces hostname or reachability errors.

**Fix:** In Dashboard → **Connect** → choose **Session pooler** (Shared pooler / Supavisor, port **5432**, host `aws-*..pooler.supabase.com`, username `postgres.<project-ref>`). Copy that URI into **`SOMA_DATABASE_URL`**. Alternatives: enable the **IPv4 add-on** (paid) so the direct host speaks IPv4, or confirm IPv6 works from your network ([test-ipv6.com](https://test-ipv6.com/)).

## Phase 5–6: running `run_daily_pipeline` locally

The entry point is `pipeline.orchestration.run_daily_pipeline` with a constructed
`DailyPipelineIO` (LLM callable, DB loaders, optional persisters, thresholds map,
delivery callback, etc.). There is **no** dedicated CLI in-repo yet.

- **Best reference (offline):** `tests/test_orchestration.py` — fakes for every
  boundary, no AWS or Postgres. Run it with **`python3 tests/test_orchestration.py`**
  (forwards to pytest) or **`pytest tests/test_orchestration.py -q`**.
- **Production-shaped wiring:** `infrastructure/lambda/briefing/handler.py` —
  psycopg2, boto3 (Secrets Manager for DB/Anthropic/SES, SSM for rule thresholds),
  then `run_daily_pipeline` per user.

After `pip install -e ".[dev]"`, you can exercise the orchestrator from a small
script or `python -c` by copying the `DailyPipelineIO` construction pattern from
those files. For email and LLM behavior without AWS, use `ENV=local` and the
injected fakes from the tests.

For **AWS CDK** (`make cdk-synth`), the Lambda dependency layer is built with **local**
``pip`` (no Docker); use Python **3.14** and PyPI access — see [README.md](../../README.md) § AWS CDK.

## Related docs

- [integrations-checklist.md](./integrations-checklist.md) — services to integrate (confirm with you).  
- [implementation-plan.md](./implementation-plan.md) — Phase 0 updated for non-Docker workflow.  
- [../schema/README.md](../schema/README.md) — schema overview + link to full SQL.

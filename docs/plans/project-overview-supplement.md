# Soma — Project Overview Supplement (June 2026)

This document **does not replace** [project-overview.md](./project-overview.md). It records **corrections**, **timing/orchestration recommendations**, **internal inconsistencies** found when validating the overview against the current repo and engineering reality, and **questions** for the product owner.

**Current repo state:** Planning docs, `.cursor/rules/`, **`.bruno/README.md`**, **`schema/soma-planned-schema.sql`**, **`docs/schema/`**, **`pipeline/`** package + tests, minimal **AWS CDK (Python)** under **`infrastructure/`** (`SomaStagingStack`, `SomaProdStack`). Full Lambdas / S3 / SES wiring still future work.

---

## 0. Local development without Docker (owner preference)

The original [project-overview.md](./project-overview.md) assumed **Docker** for local Postgres and **LocalStack** for AWS mocks. That buys parity with CI and offline S3, but adds operational overhead.

**Your preference:** avoid Docker. The canonical write-up is **[local-dev-and-tooling.md](./local-dev-and-tooling.md)** — Bruno for vendor APIs, **hosted Supabase** for schema/RLS/PostgREST validation, optional native Postgres via Homebrew if you ever need it without Supabase. AWS pieces can be exercised against **staging** when needed instead of LocalStack.

---

## 1. Pipeline timing (your requested change)

### Problem

The overview’s **Daily Pipeline Flow** uses **5:50 → 5:55 → 6:00** (five- and ten-minute gaps). That is **too tight** for:

- Slow or paginated API responses (Strava, Google).
- **Webhook-delivered** Apple Health batches that may arrive **unpredictably** relative to a fixed cron.
- Normalization + wide-table upserts + feature computation on growing history.
- Cold Lambda starts, VPC (if added), Secrets Manager / SSM latency, and retries.
- Operational margin: you want the briefing to reflect **“data as of X”** not “whatever finished in the last 300 seconds.”

### Recommendation

Pick one of these **documented** strategies (implementation can follow in **Phase 5** of the implementation plan):

1. **Single orchestrated pipeline** (preferred for clarity): one scheduled start (e.g. **04:00 local**), run steps **sequentially** inside one Lambda or Step Functions state machine: ETL (all sources) → normalize → `daily_health_metrics` / `daily_features` → rules → anomalies → LLM → persist → SES. Target email by **06:30** or **07:00** with internal slack, not wall-clock cron spacing.
2. **Wide staggered crons** (simpler but looser): e.g. ETL **04:00**, features **05:30**, briefing **07:00** — **90–180 minutes** between ingest *window start* and email, with explicit “ingest window closes at” semantics.
3. **Event-driven**: ETL completion emits an event; downstream jobs wait for **completion + optional delay** (SQS visibility / Step Functions Wait). Best if you split Lambdas and want backpressure.

Also define **cutoff policy** for the briefing, e.g.:

- “Briefing uses **last completed** `daily_features` row for `briefing_date` D,” or  
- “Briefing includes webhook data received **until D 06:15** local; late data waits for tomorrow / optional afternoon digest.”

---

## 2. Validity check: what still holds

| Topic | Verdict |
|-------|---------|
| Raw-to-S3 before normalize | **Sound** — keep as non-negotiable. |
| Service-agnostic schema | **Sound** — good long-term bet. |
| Multi-user via Supabase Auth + RLS | **Sound for user-facing queries** — see §3 for batch jobs. |
| Hybrid rules + LLM narration | **Sound** — matches safety and auditability goals. |
| Staging vs prod isolation | **Sound** — standard practice. |
| Fitbit → Google Health direction | **Plausible** — confirm against current Google Health / Fitbit migration docs at implementation time. |

---

## 3. RLS wording vs Lambda reality

The overview states that API and query paths “**never need to filter by `user_id`**” because RLS enforces it. That is **true only** when every query uses a **user-scoped JWT** (anon/authenticated role).

**Typical Lambda ETL / batch jobs** use the **service role** or a direct Postgres connection with elevated privileges — in that case **RLS is often bypassed**. You must still:

- Set `user_id` correctly on every insert/update, and  
- **Scope** reads/writes by `user_id` in SQL when the job handles multiple tenants, **or** run separate invocations per user with locked-down credentials.

**Improvement:** In implementation docs, split into:

- **Path A — User session (RLS enforced):** Streamlit/Next with user JWT.  
- **Path B — Batch job (explicit `user_id`):** Lambda with service role; RLS optional defense-in-depth but not relied upon alone.

---

## 4. Internal inconsistencies in project-overview.md (fix at build time)

| Item | Issue |
|------|--------|
| **SSM paths** | Mix of `/soma/{env}/{user_id}/rules/`, `/soma/{user_id}/rules/`, `/soma-staging/rules/...`, and local `/soma/rules/...`. **Pick one convention** and encode it in **CDK** (construct props / constants) + seed scripts. |
| **`daily_briefings` example** | Python upsert uses `metrics`; table DDL uses `features_json` / `recommendations` / `flags` — **align field names** when coding. |
| **“No archival” vs Phase 4** | Narrative says keep Supabase hot without Parquet complexity; phased plan mentions **Parquet archive** and NRC cold archive — **contradictory**. Choose: (a) no Parquet, or (b) Parquet only for raw exports / analytics — then update the narrative phase. |
| **`.env.local` `SUPABASE_URL`** | Shown as `postgresql://...` — valid for psycopg2; **supabase-py** often expects project URL + keys. Document **two variables** if both are used: `DATABASE_URL` vs `SUPABASE_URL` REST. |
| **`auth.uid()` in plain Postgres** | RLS policies reference `auth.users` / `auth.uid()`. A **plain local Postgres** (including Homebrew) has no `auth` schema unless you mirror it. Prefer **Supabase-hosted dev** or **Supabase CLI linked project** for RLS tests; see [local-dev-and-tooling.md](./local-dev-and-tooling.md). |
| **Anthropic model IDs** | Specific dated model strings will go stale — **pin in config** and refresh periodically. |

---

## 5. Data source / integration notes

- **Apple Health via Health Auto Export → webhook:** ingestion may be **bursty** and **late** relative to a morning briefing — scheduling must account for this (§1).
- **NRC “scraper”:** correctly flagged fragile — treat as one-time historical import only.
- **Hevy “Pro required”:** verify current Hevy API access tier when subscribing.

---

## 6. Agents & plugins when building (operational)

- **Cursor rules** (already present): keep stack conventions synchronized with code.  
- **Skills:** Supabase + Postgres best-practices for migrations/RLS; AWS Lambda for handlers; **AWS CDK (Python)** for `infrastructure/` (or repo-chosen CDK package path).  
- **MCP:** Use Supabase MCP for **staging** inspection (logs, advisors) — avoid applying migrations to prod from automation without explicit human gate.  
- **Code review:** Use security-focused review after secrets, SES, and any public HTTP endpoint (webhooks) exist.

---

## 7. Questions for product owner

1. **Briefing local time:** Is **06:00** fixed, or should `user_settings.briefing_time` drive EventBridge **per-user** schedules from day one? (Per-user schedules multiply EventBridge rules or require a dispatcher.)
2. **Single-user MVP:** Confirm first production user is **only you** — do you still want **full multi-tenant schema** on day one, or minimal tables + RLS added when user two appears?
3. **Apple Health:** Is webhook payload **always** the canonical biometric source, or do you want **Google Health** as redundant backup for sleep/HRV?
4. **Weekly Sonnet scan:** Budget cap per week? OK to **skip** if weekly API cost spikes?
5. **Orchestration preference:** Step Functions vs single “fat” Lambda vs SQS chain — any **hard constraint** (e.g. “must stay under $X/month” / “no Step Functions”)?
6. **Archival:** Confirm **no Parquet** for v1 to match “keep it simple” narrative, or explicitly want cold archive for NRC dump only.

---

## 8. CI/CD: GitHub Actions → one AWS account

You can deploy **staging** and **prod** into the **same** AWS account: use **different resource names** (separate **CDK stacks** or **CDK stages** per environment), **different S3 buckets / Lambda names / SSM paths**, and **GitHub Environments** so prod deploys require approval. Database isolation remains **two Supabase projects** (or branches), not two AWS accounts. Full workflow outline: [implementation-plan.md](./implementation-plan.md) **Phase 4**.

---

*This supplement should be updated as decisions land; keep project-overview.md as historical context unless you choose to merge them later.*

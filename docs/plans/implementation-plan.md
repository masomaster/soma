# Implementation Plan: Soma (Personal Health OS)

**Status:** Phase 0 scaffold complete (`pipeline/`, `pyproject.toml`, tests, `AGENTS.md`, `schema/migrations/` convention). Phase 1 (API validation) onward pending.  
**Companion docs:** [project-overview-supplement.md](./project-overview-supplement.md) (timing, doc validation, agents/plugins), [local-dev-and-tooling.md](./local-dev-and-tooling.md) (no-Docker workflow, Bruno, Supabase REST), [integrations-checklist.md](./integrations-checklist.md) (sources to confirm).  
**Historical / detailed vision:** [project-overview.md](./project-overview.md) (unchanged source conversation).

### How we work (agents / humans)

This is a **greenfield** codebase: refactor, rename, and restructure when it improves clarity, tests, or operability. The old “smallest possible diff only” bar was for **surgical edits in mature repos** — it is **not** a goal here. Still avoid unrelated drive-by churn in a single PR when it obscures review.

---

## Requirements Restatement

Build a **multi-tenant-ready**, **environment-isolated** pipeline that:

1. **Ingests** fitness/health data from external APIs and webhooks, **writes raw JSON to S3 first**, then normalizes into **Supabase Postgres** tables with **RLS** and `user_id` on every domain table.
2. **Derives** daily wide metrics and `daily_features`, runs a **deterministic rules layer** (thresholds externalized, e.g. SSM) and **statistical anomaly** detection, optionally **weekly** LLM-assisted pattern scan.
3. **Synthesizes** a daily coaching note via LLM that **narrates pre-computed signals** (no free-form reasoning over raw event dumps as the sole logic).
4. **Delivers** the briefing (e.g. SES email in cloud envs; stdout/local log when `ENV=local`).
5. Supports **local development without Docker** (Bruno + hosted Supabase for schema/API validation), plus **staging** and **production** with promotion discipline. See [local-dev-and-tooling.md](./local-dev-and-tooling.md).

Non-goals for initial phases: polished NL query UI (deferred), native iOS app (optional later), replacing the whole stack with a persistent “agent runtime.”

---

## Phases

### Phase 0 — Repository & agent/plugin harness (no cloud)

- Add **Python package layout** (`pyproject.toml`, `pipeline/`) aligned with `.cursor/rules/soma.mdc` (logging, type hints, thin handlers later). Target **Python 3.14+** locally and in CI.
- Add **`schema/migrations/`** convention (numbered SQL) when implementation starts; until then **`schema/soma-planned-schema.sql`** is the planned DDL (see [docs/schema/README.md](../schema/README.md)).
- **Cursor:** keep `.cursor/rules/soma.mdc` and `sql.mdc` as source of truth; add **AGENTS.md** (or extend README) describing which **subagents/skills** to use per task class (e.g. Supabase skill for RLS/migrations, aws-lambda for handlers, **AWS CDK** for infra).
- **Plugins:** document intended use (Supabase MCP for remote debugging only; AWS docs / CDK patterns for IaC) — no requirement to wire MCP in Phase 0.
- **Deliverable:** **Bruno** collections under `.bruno/` (see [.bruno/README.md](../../.bruno/README.md)); documented **venv** + env vars; optional **Makefile / justfile**; no Docker requirement. Seed data can target **Supabase dev** via SQL or a small script once migrations exist.

### Phase 1 — Vendor API validation (before Supabase migrations)

**Why here:** `schema/soma-planned-schema.sql` is an educated guess. **Migrations should reflect real payloads** — otherwise you fight nullable columns, wrong uniqueness keys, and metric enums after data is already in Supabase. API work comes **first**; schema wiring is **Phase 2**.

- Call each priority source with **Bruno** (and/or tiny throwaway scripts) using **real** credentials (never commit secrets; use env / Bruno secrets).
- Drop **redacted** JSON samples under `tests/fixtures/<source>/` and note pagination, timestamps, units, and edge cases in `docs/plans/integrations-checklist.md` or per-source `docs/data/*.md` if you split files later.
- Decide **dedup keys** (`source_id` patterns) and **canonical metric names** from actual fields — update the **planned** SQL file if needed before generating migration SQL.
- **Optional:** hit Supabase **REST** with a scratch table only if you want to validate auth headers — **not** required to apply the full domain schema yet.
- **Deliverable:** checklist complete for at least **one** strength source and **one** biometric/cardio path you will ship first; you are ready to freeze `0001_*.sql` in Phase 2.

### Phase 2 — Schema + RLS + “who is the database client?”

- Implement **`schema/migrations/`** (e.g. `0001_initial.sql`) from the **validated** model — evolved from `schema/soma-planned-schema.sql` after Phase 1.
- Apply to **Supabase staging**; document promotion to prod.
- **Decide explicitly:** Lambda/data jobs use **service role** (bypass RLS) vs **per-user JWT**. If service role: document that **application code must scope by `user_id`** for job safety; RLS remains for **end-user** paths (Streamlit/Next later). Reconcile this with overview wording — do not pretend RLS absolves batch jobs.
- **RLS tests:** as in overview — second user’s JWT sees zero rows.
- **Deliverable:** migration set + short doc on DB access patterns.

### Phase 3 — Raw S3 + one ETL adapter (vertical slice)

- S3 raw path: `raw/{user_id}/{source}/{YYYY-MM-DD}/{timestamp}.json` (match workspace rule).
- **Hevy first** (or Strava if OAuth setup is easier for you): fetch → **raw write** → normalize → upsert with **`ON CONFLICT (user_id, source_id) DO NOTHING`** (per workspace rule).
- Local raw writes: **optional** (staging S3 bucket with a `dev/` prefix, or defer S3 until first Lambda); LocalStack/Docker **not** assumed — add only if you need offline S3.
- **Deliverable:** one adapter with unit tests that load **Phase 1 fixtures** + any extra edge-case files under `tests/fixtures/`.

### Phase 4 — GitHub Actions → AWS (continuous deployment)

**Goal:** pushes (or merges) trigger **test + deploy** into **your** AWS account. Staging vs prod **does not require** two AWS accounts — use **logical isolation** inside one account.

- **Auth:** GitHub **OIDC** → AWS (`aws-actions/configure-aws-credentials` with `role-to-assume`) so the repo never stores long-lived `AWS_ACCESS_KEY_ID` / secret pairs if avoidable.
- **Single-account staging + prod:** separate **CDK stacks** or **CDK `Stage`s** (e.g. `SomaStagingStack` / `SomaProdStack`, or one app with `env` context), distinct **resource name prefixes** (e.g. `soma-staging-*` vs `soma-prod-*`), separate **S3 buckets**, **Lambda names**, **SSM trees** (`/soma/staging/...` vs `/soma/prod/...`), and **IAM resource scoping** so staging deploy roles cannot mutate prod ARNs (tighten policies as ARNs stabilize). **Supabase** stays two projects (staging DB vs prod DB) — that isolation is outside AWS.
- **Branch / workflow shape (suggested):**
  - **`ci.yml`:** every PR + push to main — `pytest`, lint/type if added, **no** deploy to prod alone.
  - **Staging deploy:** e.g. push to `main` runs **`cd infrastructure && cdk deploy SomaStagingStack`** (after `pip install -e ".[cdk]"` or `pip install -r infrastructure/requirements.txt`) **after** CI passes.
  - **Prod deploy:** **manual** `workflow_dispatch` and/or **GitHub Environments** with **required reviewers**, or deploy only on **release tags** `v*`, so prod is never silently overwritten by a bad push — e.g. **`cd infrastructure && cdk deploy SomaProdStack`** only from protected workflow.
- **Secrets:** GitHub Actions **secrets** / **environments** for Supabase deploy URLs, CDK context or asset publishing if needed, etc.; AWS access via OIDC role only where possible.
- **Deliverable:** `.github/workflows/` with the above split; short `docs/plans/ci-aws.md` (optional) or a **Runbook** section in README listing required GitHub Environment + IAM OIDC setup steps.

### Phase 5 — Scheduling + orchestration (fix the “5–10 minute” problem)

- Replace **tight multi-cron** (5:50 / 5:55 / 6:00) with either:
  - **One daily pipeline** (single Lambda or Step Functions) with **internal ordered steps** and a **single scheduled start** well before desired email time, **or**
  - **Event-driven chain:** ETL completion → SQS/EventBridge → features → briefing, with **visibility timeouts** and **DLQ**, **or**
  - **Wider stagger** (e.g. 60–120+ minutes between ingest window close and briefing) if cron simplicity is preferred.
- **Ingest latency:** webhook sources (Apple Health export) may land **after** “ETL cron”; define **cutoff** (“briefing uses data as of T-2h local”) or **re-run** policy.
- **Deliverable:** diagram + **CDK-defined** EventBridge (or Step Functions) matching chosen pattern; SLAs documented in supplement.

### Phase 6 — Features + rules + briefing (still staging-first)

- Populate `daily_health_metrics` from `biometrics`; compute `daily_features`.
- Rules engine **Option A** (hand-coded + externalized thresholds). Unify **SSM path** convention early: `/soma/{env}/{user_id}/rules/...` (fix overview inconsistencies at implementation time).
- Briefing Lambda: build prompt from **flags + features + anomalies + guidelines**; call Haiku; persist `daily_briefings`; SES in staging with `[STAGING]` subject.
- **Deliverable:** end-to-end staging runbook + CloudWatch alarms on failures.

### Phase 7 — Production + more sources

- Promote **CDK** stacks + Supabase migrations to prod; secrets per env/user.
- Add sources in order of **dependency / risk** (e.g. Strava OAuth, Apple webhook adapter, Renpho, Google Health before Fitbit sunset).
- **Deduplication / source priority** as in overview — implement explicitly in code or small config table.

### Phase 8 — Anomaly layer

- Statistical anomalies daily; weekly Sonnet scan optional behind feature flag.
- Persist to `anomaly_events`; include in briefing prompt per overview.

### Phase 9 — Query frontend (optional)

- Streamlit spike → Next.js PWA if validated; text-to-SQL only with **schema-bound** prompts and **read-only** role — threat model in supplement.

---

## Dependencies

- **AWS:** IAM, S3, Lambda, EventBridge (or Step Functions), SES, Secrets Manager, SSM, CloudWatch. **IaC:** **AWS CDK v2 (Python) only** — no Terraform or SAM for Soma; single-account staging/prod OK via separate CDK stacks/stages.
- **Supabase:** staging + prod projects (or single project + branches if you adopt that model — decide explicitly).
- **Anthropic:** API keys, spend limits; **model IDs** pinned in config (refresh names when implementing).
- **External APIs:** Hevy Pro API, Strava OAuth, Health Auto Export behavior, Google Health Connect / OAuth, Renpho, CalDAV.
- **Local:** Python **3.14+** on the host, **Bruno**, **Supabase CLI** (optional) or Dashboard-only workflow; **no Docker** unless you later choose LocalStack or containerised CI. **GitHub Actions** should pin **Python 3.14** in `setup-python` when workflows are added (Phase 4).

---

## Risks

| Severity | Risk |
|----------|------|
| **High** | **RLS vs batch jobs:** service role bypasses RLS — wrong `user_id` or missing filter can corrupt or leak data across tenants. |
| **High** | **Webhook + cron mismatch:** briefing runs before Apple Health payload arrives → stale coaching. |
| **Medium** | **SSM path drift** between overview sections (`/soma-staging/...` vs `/soma/{env}/{user_id}/...`) → misconfigured thresholds in prod. |
| **Medium** | **Supabase “URL” confusion:** REST URL vs Postgres connection string for different clients — misconfiguration in Lambda. |
| **Medium** | **OAuth token refresh** (Strava, Google) — secrets rotation and failure handling. |
| **Low** | **Cost estimate** in overview is rough; Secrets Manager and API usage can exceed early expectations at scale. |
| **Medium** | **Lambda runtime vs `requires-python`:** AWS may lag the newest CPython. Before locking **3.14** on Lambda, confirm **managed runtime** support or use a **container image** you build in CI (Phase 4 / Lambda packaging). |

---

## Existing Patterns to Follow

- **Workspace rules:** `.cursor/rules/soma.mdc` — raw-before-normalize, SSM thresholds, RLS discipline, canonical metric names, adapter return shape.
- **SQL style:** `.cursor/rules/sql.mdc` when writing migrations.
- **Docs:** `README.md` for high-level; keep long-form in `docs/plans/` with supplements for deltas.
- **Refactors:** Prefer consistency across the `pipeline/` package over preserving the first draft of a module layout.

---

## Agents & Plugins (when building)

| Work type | Suggested agent / plugin |
|-----------|-------------------------|
| Postgres migrations, RLS, advisors | Supabase skill + Supabase MCP (read-only / staging first). |
| Lambda, EventBridge, Step Functions | `aws-lambda` / `aws-serverless-deployment` skills; AWS MCP for IaC snippets if enabled. |
| AWS CDK (Python) app layout | **deployment-engineer** / CDK patterns; AWS docs — **Terraform not used** for Soma. |
| GitHub Actions → AWS (OIDC, deploy) | **deployment-engineer** / **deploy-ci-cd-agent** patterns; AWS IAM OIDC trust for `token.actions.githubusercontent.com`. |
| Security review before prod | Dedicated security-review / ce-security-reviewer after auth + SES + secrets land. |
| E2E of email path | Optional later: staging integration tests or manual runbook — not Phase 0. |

Use **planner → implement** workflow: keep this file updated when phases complete.

---

## Out of Scope (unless you ask)

- **Terraform or AWS SAM** for Soma AWS resources — use **CDK Python** only (keeps one language with `pipeline/`).
- Rewriting `project-overview.md` in place (use supplement for corrections).
- Parquet cold archive / “second query engine” until retention or cost proves necessary (overview itself is mixed on Phase 4 archival — pick one story).
- OpenClaw or always-on agent hosts (already “archived” in overview — aligned).
- Nike Run Club as ongoing integration (historical export only).

---

## Estimated Complexity

**High** for full multi-source + orchestration + RLS-correct batch design — roughly **80–160+ hours** spread across evenings/weekends (depends on OAuth sources and operational polish). **Medium** for a credible **Hevy + local + staging email** vertical slice — roughly **24–40 hours**.

---

## Open Questions (need your input)

See [project-overview-supplement.md](./project-overview-supplement.md) § Questions for product owner.

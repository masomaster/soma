# Implementation Plan: Soma (Personal Health OS)

**Status:** Phase 0 scaffold complete (`pipeline/`, `pyproject.toml`, tests, `AGENTS.md`, `schema/migrations/` convention). **Phase 1 complete:** Hevy `GET /v1/workouts` validated against live API + [Swagger docs](https://api.hevyapp.com/docs/); redacted fixtures and shape tests under `tests/fixtures/`; Bruno `hevy/list-workouts`; [integrations checklist](./integrations-checklist.md) signed off for ship-first strength + biometrics rollup. **Phase 2 (repo deliverables) complete:** `schema/migrations/0001_initial.sql` (RLS + grants + Hevy `superset_id`), [db-access-patterns.md](./db-access-patterns.md), migration RLS contract tests. **Phase 3 (repo slice) complete:** `pipeline/raw_storage.py` (raw key layout), `pipeline/adapters/hevy.py` (fetch / raw callback / normalize), `pipeline/strength_upsert.py` (`ON CONFLICT DO NOTHING`), `tests/test_hevy_adapter.py`. **Phase 4 (repo deliverables) complete:** `.github/workflows/ci.yml` (reusable: `pytest` 3.14 + `cdk synth`), `deploy-staging.yml` (push to `main` → `cdk deploy SomaStagingStack`), `deploy-prod.yml` (tag/dispatch + environment approval → `cdk deploy SomaProdStack`), all via **GitHub OIDC → AWS** (no stored keys); setup runbook [ci-aws.md](./ci-aws.md). **Phase 5 (repo deliverables) complete:** `pipeline/orchestration.py` (single daily pipeline, ordered isolated steps) + CDK `DailyBriefingPipeline` (EventBridge daily `cron` → briefing Lambda) wired into both stacks. **Phase 6 (repo deliverables) complete:** `pipeline/features.py` (biometrics rollup + deterministic `daily_features`), `pipeline/rules.py` (Option A rules, thresholds from SSM `/soma/{env}/{user_id}/rules/`), `pipeline/briefing.py` (prompt + injected LLM, narrates pre-computed signals), `pipeline/delivery.py` (stdout local / SES otherwise), `pipeline/persistence.py` (allow-listed `DO UPDATE` upserts), `pipeline/clients.py` (Anthropic/SES/SSM/Postgres adapters), thin `infrastructure/lambda/briefing/handler.py`; offline unit tests for all. **Phase 6.6 complete:** briefing quality bar (rules vs sparse recovery, prompt guardrails, HTML email + optional `BRIEFING_EMAIL_DASHBOARD_URL`, [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md), [briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md)) — see § Phase 6.6. **Phase 10 (scheduled):** training guidelines + expert corpus, SES/HTML tuning, and prompt templating **after Phase 7** (additional sources on staging) and **Phase 8** (anomalies in prompt)—see Phase 10. **Production cutover and staging cost posture** are explicitly **Phase 11** so Phase 7 can stay a single-environment integration track. **Operator next:** apply `0001` to **Supabase staging** if not already; run the [ci-aws.md](./ci-aws.md) one-time AWS/GitHub setup (OIDC provider, `soma-github-deploy` role, `cdk bootstrap`, `staging`/`production` environments) to enable live deploys; build the briefing Lambda layer/container (`pipeline` + `psycopg2`) and set its secrets (`DB_CONNECT_STRING`, `ANTHROPIC_API_KEY`, `SES_SENDER`, verify SES sender); wire `raw_put` to real S3 in Lambda when data-plane resources land.  

**Companion docs:** [project-overview-supplement.md](./project-overview-supplement.md) (timing, doc validation, agents/plugins), [local-dev-and-tooling.md](./local-dev-and-tooling.md) (no-Docker workflow, Bruno, Supabase REST), [integrations-checklist.md](./integrations-checklist.md) (scope + Phase 1 payload notes), [db-access-patterns.md](./db-access-patterns.md) (keys, RLS vs service role, migration apply order), [workload-indicators.md](./workload-indicators.md) (weekly/monthly training load — evidence-backed v0/v1). **Aggregation + change detection:** [§ Signal pipeline](#signal-pipeline-where-intelligence-lives) below (tiers 1–4; aligns with `.cursor/rules/soma.mdc` — LLM narrates pre-computed signals).  
**Historical / detailed vision:** [project-overview.md](./project-overview.md) (unchanged source conversation).

### How we work (agents / humans)

This is a **greenfield** codebase: refactor, rename, and restructure when it improves clarity, tests, or operability. The old “smallest possible diff only” bar was for **surgical edits in mature repos** — it is **not** a goal here. Still avoid unrelated drive-by churn in a single PR when it obscures review.

---

## Requirements Restatement

Build a **multi-tenant-ready**, **environment-isolated** pipeline that:

1. **Ingests** fitness/health data from external APIs and webhooks, **writes raw JSON to S3 first**, then normalizes into **Supabase Postgres** tables with **RLS** and `user_id` on every domain table — including **historical / backfill** loads (as far back as each vendor allows), not only “from today onward,” so the DB and features layer can warm up correctly.
2. **Derives signals in tiers** (see [Signal pipeline](#signal-pipeline-where-intelligence-lives)): **Postgres-first aggregates** where they pay off, **deterministic statistics** (Z-score / IQR / EWMA-style drift) in Python on Lambda, optional **learned cross-metric patterns** persisted in Postgres, then **hand-coded rules** (SSM thresholds) — all before the LLM. Optional **weekly** Sonnet pass only for narrative pattern hints behind a flag, not as the primary numeric anomaly engine.
3. **Synthesizes** a daily coaching note via LLM that **narrates pre-computed signals** — structured **today + flags + trends + anomalies + active patterns**, not raw event dumps or “do the math in prose.”
4. **Delivers** the briefing (e.g. SES email in cloud envs; stdout/local log when `ENV=local`).
5. Supports **local development without Docker** (Bruno + hosted Supabase for schema/API validation), plus **staging** and **production** with promotion discipline. See [local-dev-and-tooling.md](./local-dev-and-tooling.md).

Non-goals for initial phases: unconstrained natural-language query over raw tables without schema binding and a hardened read path (Phase 9 targets a **small dashboard + bounded queries** instead); native iOS app (optional later); replacing the whole stack with a persistent “agent runtime.”

---

## Signal pipeline: where intelligence lives

**Principle:** Numeric trend and outlier work belongs in **SQL and scipy**, not in the LLM. Haiku (daily briefing) and optional Sonnet (weekly) **explain and sequence** signals that are already computed, reproducible, and auditable. That matches the existing Phase 6 bar (`pipeline/features.py`, `pipeline/rules.py`, `pipeline/briefing.py`) and extends it with explicit **aggregation** and **statistical** layers before prompt construction.

**End state — briefing input shape (conceptual):** The model receives a bounded JSON object (serialized into the prompt), not raw `biometrics` / `strength_events` rows. Example structure:

```json
{
  "today": {
    "hrv_rmssd": 58,
    "sleep_hours": 6.2,
    "readiness": null,
    "training_load_cardio_minutes_7d": 120
  },
  "rules_flags": [{"code": "HIGH_SLEEP_DEBT", "detail": "…"}],
  "stat_anomalies": [
    {
      "metric": "hrv_rmssd",
      "value": 42,
      "baseline_30d_mean": 55,
      "z_score": -2.3,
      "anomaly_type": "statistical",
      "method": "z_score"
    }
  ],
  "trends": [{"metric": "sleep_hours", "direction": "declining", "window_days": 5}],
  "active_patterns": ["sleep < 6h → next-day HRV lower (confirmed n=12)"]
}
```

Actual keys evolve with `daily_features` / rules enums; the contract is **signals first, narrative second**.

### Layer 1 — SQL aggregation (stay in Postgres as long as possible)

**Goal:** By the time a job reads “history,” wide tables already contain rolling windows, deltas, and PR-style summaries where those are cheaper or clearer in SQL than in Python.

**Fit with Soma today:** Phase 6 already persists **`daily_health_metrics`** (wide biometric day rows) and **`daily_features`** (rolling strength/cardio/recovery/training-load columns from `pipeline/features.py`). Treat that as **v0 of Layer 1** implemented in application code. **v1+:** move stable rolling metrics into Postgres so a single `SELECT` powers both the stats job and future dashboard/API:

- **Window functions** for rolling **7 / 28 / 90** day means (and optional stddev) per canonical metric / feature column — aligned with [workload-indicators.md](./workload-indicators.md) (trailing windows first; ISO calendar weeks remain a product choice).
- **Day-over-day and week-over-week deltas** on key series (`hrv_rmssd`, `sleep_hours`, `resting_hr`, selected `training_load_*` / `effort_*`).
- **PR tracking:** `MAX()` (or best-set logic) over `strength_events` history per `(user_id, exercise_name)` (and optionally muscle group); store **latest PR date + value** in a small **`exercise_pr_peaks`** or enrich `daily_features` — pick one denormalization story to avoid N+1 in briefing prep.
- **Streaks:** consecutive days above/below a threshold (thresholds still from SSM-backed rules config; SQL computes streak length from `daily_features` / `daily_health_metrics`).
- **Execution options:** (a) extend the **daily Lambda** pipeline step to `REFRESH MATERIALIZED VIEW` or run SQL that upserts **`metric_baselines`** / **`daily_signal_snapshots`** rows; (b) **Supabase `pg_cron`** to refresh materialized views on a schedule if you want aggregation without an extra Lambda invocation. **RLS:** any new table keeps `user_id` + same isolation pattern as existing domain tables; batch jobs use **service role** and explicit `user_id` per [db-access-patterns.md](./db-access-patterns.md).

**Non-goals here:** a second time-series database (see [Out of scope](#out-of-scope-unless-you-ask)); shipping every statistic as a materialized view before measuring query cost (start with targeted SQL or incremental table upserts).

### Layer 2 — Statistical anomaly detection (Lambda + NumPy / SciPy)

**Goal:** Deterministic flags for outliers and drift; persist as first-class rows for briefing, dashboard, and audits.

**Implementation:** New `pipeline/` module (e.g. `pipeline/stat_anomalies.py`) invoked from the **daily pipeline** after features + rules inputs are available (or in parallel where dependencies allow). Load **Layer 1 outputs** + recent `daily_health_metrics` / `daily_features` windows via psycopg2 (already in the Lambda layer). Use **vectorized** NumPy and, where helpful, **SciPy** / **statsmodels** — all compatible with a **Lambda layer** if dependency size is managed (strip tests, prefer wheels).

| Method | Use in Soma |
|--------|----------------|
| **Z-score** vs rolling mean/std | Approximately normal recovery metrics: `hrv_rmssd`, `sleep_hours`, `resting_hr` — flag beyond ~2σ from **user’s** rolling baseline (respect sparse-data gates already used in rules; do not fire when observation count is below a configured minimum). |
| **IQR** (e.g. 1.5× fence) | Skewed counts/volumes: `steps`, `active_cal`, `training_load_*`, session counts. |
| **EWMA / residual** | Slow **drift** (e.g. sleep creeping down) where no single day crosses a Z threshold; emit `trend` records as well as or instead of hard “anomaly” rows. |

**Persistence:** Use existing **`anomaly_events`** (`0001_initial.sql`): `anomaly_type` = `'statistical'`, `metric`, `description` (human-readable one-liner), **`context_json`** for `{ "z_score", "baseline", "value", "method", "window_days" }`. Idempotent **`ON CONFLICT`** or delete-and-insert for `(user_id, detected_date, metric, anomaly_type, method)` if you add a uniqueness migration — avoid duplicate rows on pipeline retries.

**Orchestration:** Extend `run_daily_pipeline` (see `pipeline/orchestration.py`) with an explicit step **after** rules (or after features if rules depend only on same-day features): e.g. `statistical_anomalies` → then `generate_briefing` so the prompt includes today's statistical rows.

### Layer 3 — Pattern library (Postgres, growing over time)

**Goal:** Store **cross-metric correlations** and lag relationships discovered offline so the LLM can cite “days like today historically led to X” without inventing correlations.

- **Table (new migration):** e.g. **`metric_patterns`** — `user_id`, `metric_a`, `metric_b`, `lag_days`, `correlation`, `effect_size` or sample `n`, `detected_at`, `last_confirmed_at`, `status` (active / stale). **Weekly** EventBridge schedule → Lambda job: pull last N days of aggregates, run pairwise / targeted tests, **upsert** rows; cap row count per user to keep prompts bounded.
- **Optional — semantic similarity:** Supabase **`pgvector`**: embed a compact **daily health summary** JSON into a vector and retrieve **k nearest past days** for narrative context (“similar historical days”). Useful when explicit pairwise correlation is too brittle; still **read-only context** for the LLM, not a replacement for Layer 2.
- **Optional — seasonality:** **Prophet** (or simpler weekly seasonality detrend) in a **separate** job or later phase when training **cadence** (e.g. heavy lower-body Mondays) is stable enough to justify model fit cost in Lambda or a one-off container — not required for v1.

**Weekly Sonnet “pattern scan”** (already envisioned in overview): remains **secondary** — e.g. suggest copy for long-form insights or propose candidate rows for human review — **not** the source of truth for numeric outliers.

### Layer 4 — LLM synthesis (existing briefing path)

**Goal:** `pipeline/briefing.build_prompt` / `generate_briefing` consume **Layer 1–3 outputs + rules flags**, with guardrails already from Phase 6.6. Extend prompt assembly to inject **`stat_anomalies`**, **`trends`**, and **`active_patterns`** blocks from structured data (not CSV dumps of events).

**Architecture unchanged at the edge:** EventBridge → Lambda → Supabase → Haiku → SES / stdout — correct shape; the work is **what gets computed before the LLM call**.

### Explicitly out of scope for this pipeline

- **Amazon SageMaker / Lookout for Metrics** — managed ML anomaly SaaS; wrong cost and complexity for a personal OS.
- **TimescaleDB / InfluxDB** — Postgres + windows + indexes per [supabase-postgres-best-practices](../../.agents/skills/supabase-postgres-best-practices/SKILL.md) skill is sufficient; a second TSDB creates sync and ops burden.
- **Prompting the model on raw time-series** and asking it to find outliers or correlations — acceptable only as **tiny exemplars** for education, never as the primary detection mechanism.

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

**Phase 1 closure (2026-06):** Live Hevy list response confirmed `page` / `page_count` / `workouts`, exercise field **`superset_id`** (nullable int), nullable **`weight_kg`** with reps (bodyweight), ISO timestamps mixed (`+00:00` vs `Z` + ms), `routine_id` nullable. See [integrations-checklist.md](./integrations-checklist.md) § Phase 1.

### Phase 2 — Schema + RLS + “who is the database client?”

- ✅ Implement **`schema/migrations/`** — `0001_initial.sql` from the validated model (`schema/soma-planned-schema.sql` + Phase 1 `superset_id` on `strength_events`).
- **Apply to Supabase staging** (operator): Dashboard SQL, `psql`, or Supabase CLI; then **promote to prod** via your release checklist — see [db-access-patterns.md](./db-access-patterns.md).
- ✅ **Decide explicitly:** [db-access-patterns.md](./db-access-patterns.md) — Lambdas / ETL use **`service_role`** + explicit `user_id`; RLS protects **user JWT** paths.
- ✅ **RLS tests:** `tests/test_migration_rls_contract.py` asserts every domain table has RLS + `auth.uid()` policies in the migration; manual two-user REST check documented in [db-access-patterns.md](./db-access-patterns.md).
- ✅ **Deliverable:** migration + [db-access-patterns.md](./db-access-patterns.md).

### Phase 3 — Raw S3 + one ETL adapter (vertical slice)

- ✅ S3 raw path: `raw/{user_id}/{source}/{YYYY-MM-DD}/{timestamp}.json` — `pipeline/raw_storage.format_raw_object_key` (UTC); callers pass bytes to S3 / local sink via injectable `raw_put`.
- ✅ **Hevy first:** `pipeline/adapters/hevy.py` — `fetch_hevy_workouts_page` / pagination helper, `fetch_and_normalize` (raw write + normalize), `normalize_hevy_list_workouts`; `pipeline/strength_upsert.upsert_strength_events` uses **`ON CONFLICT (user_id, source_id) DO NOTHING`**.
- Local raw writes: **optional** (staging S3 bucket with a `dev/` prefix, or defer S3 until first Lambda); LocalStack/Docker **not** assumed — add only if you need offline S3.
- ✅ **Deliverable:** Hevy adapter + `tests/test_hevy_adapter.py` using **Phase 1** `tests/fixtures/hevy/get_workouts_page1_redacted.json`.

**Phase 3 closure (2026-06):** Repo slice is **complete and closed** — no further Phase 3 scope until data-plane work (real S3 from Lambdas, scheduled ETL) lands in later phases. **Operator checklist:** apply `0001_initial.sql` to Supabase staging/prod as needed; optional end-to-end smoke via `scripts/smoke_hevy.py` (`live`, `raw-disk`, `db-upsert`) per [local-dev-and-tooling.md](./local-dev-and-tooling.md) § Phase 3 (Session pooler DB URL, `SOMA_USER_ID` = Auth user UUID).

### Historical ingestion & backfill (cross-cutting)

Incremental “today only” ingestion is **not** enough for a useful coaching DB. For **each** integration (Hevy, Strava, Apple exports, Renpho, Google Health, calendar, etc.), plan and ship:

- **Initial historical pull** to the maximum depth each API/export supports (pagination, date-range filters, export bundles), always **raw to S3 first** then normalize — same adapter contract as incremental runs.
- **Idempotent writes** (`ON CONFLICT` / dedup keys) so backfill can be retried or extended without duplicate events.
- **Rate limits & batching:** respectful page sizes, backoff, optional **async job** (Step Functions, SQS + worker Lambda, or one-shot CLI) for long backfills; document per-vendor limits in [integrations-checklist.md](./integrations-checklist.md).
- **Cutoff policy:** align with briefing “data as of” rules (see Phase 5 / supplement) once historical windows are large.

**Deliverable:** per-source backfill entry point (script, job, or admin-triggered Lambda) documented next to the incremental schedule; Hevy backfill extends the existing pagination path beyond “page 1 smoke.”

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

**Phase 4 closure (2026-06):** Workflows merged (PR #2) and OIDC role + `cdk bootstrap` provisioned by operator. Security review applied: OIDC trust `sub` scoped to `environment:staging` / `environment:production` (was `repo:…:*`) so only environment-gated deploy jobs — not arbitrary branches/PRs — can assume the deploy role; `production` retains required-reviewer protection. See [ci-aws.md](./ci-aws.md).

### Phase 5 — Scheduling + orchestration (fix the “5–10 minute” problem)

- Replace **tight multi-cron** (5:50 / 5:55 / 6:00) with either:
  - **One daily pipeline** (single Lambda or Step Functions) with **internal ordered steps** and a **single scheduled start** well before desired email time, **or**
  - **Event-driven chain:** ETL completion → SQS/EventBridge → features → briefing, with **visibility timeouts** and **DLQ**, **or**
  - **Wider stagger** (e.g. 60–120+ minutes between ingest window close and briefing) if cron simplicity is preferred.
- **Ingest latency:** webhook sources (Apple Health export) may land **after** “ETL cron”; define **cutoff** (“briefing uses data as of T-2h local”) or **re-run** policy.
- **Deliverable:** diagram + **CDK-defined** EventBridge (or Step Functions) matching chosen pattern; SLAs documented in supplement.

**Phase 5 closure (2026-06):** Chose the **single daily pipeline** pattern. `pipeline/orchestration.run_daily_pipeline` runs ordered, dependency-respecting steps (rollup → features → rules → briefing → deliver) with per-step error isolation and a structured `PipelineResult`; all IO is injected (`DailyPipelineIO`) so it is pure control-flow and unit-tested. **Phase 8 extension:** insert **statistical anomaly detection** (and optional SQL snapshot refresh) **after** rules and **before** briefing — see [Signal pipeline](#signal-pipeline-where-intelligence-lives). Infra: `soma_cdk/daily_pipeline.DailyBriefingPipeline` creates one EventBridge daily `cron(0 11 * * ? *)` (well before the 06:00 local briefing) targeting the briefing Lambda; instantiated in `SomaStagingStack` / `SomaProdStack`.

### Phase 6 — Features + rules + briefing ✅ **complete (repo + CDK wiring)**

- Populate `daily_health_metrics` from `biometrics`; compute `daily_features`.
- Rules engine **Option A** (hand-coded + externalized thresholds). Unify **SSM path** convention early: `/soma/{env}/{user_id}/rules/...` (fix overview inconsistencies at implementation time).
- Briefing Lambda: build prompt from **flags + features** (and in **Phase 10**, **guidelines + expert corpus**); **statistical anomalies + trends + patterns** per [Signal pipeline](#signal-pipeline-where-intelligence-lives) in Phase 8; call Haiku; persist `daily_briefings`; SES in staging with `[STAGING]` subject.
- **Deliverable:** Operator-facing **staging readiness** (AWS + DB, not extra Python modules): verified **SES** sending identity, at least one **`user_settings`** row with `email` for the Lambda loop, and **failure notifications** wired as **CloudWatch alarms → SNS** in CDK (see **Pipeline alarms** in [infrastructure/README.md](../../infrastructure/README.md)). Inbox checks after a real briefing send: [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md). *(Alarm **delivery** to your subscribed endpoint is not yet operator-smoked; treat CDK wiring as good until you run a drill or see a real firing.)*

**Phase 6 closure (2026-06):** Logic implemented as pure, injected-IO modules (fully offline unit-tested): `features.py` (rollup + `daily_features` incl. ACWR, sleep debt, HRV suppression, readiness), `rules.py` (Option A flags; thresholds overlay SSM `/soma/{env}/{user_id}/rules/` on `DEFAULT_THRESHOLDS`), `briefing.py` (the LLM **narrates** pre-computed flags/features — never raw events), `delivery.py` (stdout when `ENV=local`, SES otherwise), `persistence.py` (sparse allow-listed `ON CONFLICT DO UPDATE` upserts), `clients.py` (Anthropic/SES/SSM/Postgres adapters), thin `infrastructure/lambda/briefing/handler.py`; offline unit tests for all. **CDK:** Lambda layer bundles `pipeline` + `psycopg2-binary` via **local** `pip` (no Docker; x86_64 Lambda); runtime secrets live in **Secrets Manager** (`soma-{env}-lambda-runtime` JSON) with a stack parameter to stop re-seeding after console edits — see `infrastructure/lambda/briefing/README.md`. **Staging operations (closed 2026-06):** SES identity verified, `user_settings` seeded for pipeline recipients, **CloudWatch → SNS** pipeline alarms **deployed** per stack (see **Pipeline alarms** in `infrastructure/README.md`). Operator work **except** an intentional alarm smoke (subscribe inbox, force a failure or synthetic alarm) is done—**assume alarm wiring is correct** until a drill or production incident proves otherwise. **Not in Phase 6 scope:** per-user `my-goals.md` / `expert-principles.md` in prompt (deferred to **Phase 10** alongside integrated operator polish). **Post-closure hardening (same release train):** `0002_daily_features_recovery_counts.sql` adds recovery coverage columns; `strength_tonnage_7d` is documented/stored as **US short tons** (lb-reps/2000); SES sends multipart **text + HTML** for readable formatting; prompts forbid inventing sleep/HRV when data is sparse.

### Phase 6.6 — Briefing quality (prompt, copy, math, email rendering) ✅ **complete**

Tighten the **operator-visible** briefing after the first SES smoke passes: strength volume units, recovery coverage vs hallucinated sleep/HRV copy, readiness when data is missing, and **HTML + plain-text** multipart email so Markdown-ish notes render in clients that prefer HTML.

- **Features / rules:** keep deterministic signals honest (e.g. tonnage as US short tons; `SPARSE_RECOVERY_DATA` when the 7-day window has no sleep or HRV rows); extend migrations (`0002_*.sql`) instead of overloading ambiguous columns.
- **Prompt:** iterate on `SYSTEM_GUIDELINES` + `build_prompt` constraints; fold in Phase 10 guideline + expert corpus when that slice ships.
- **Email:** optional richer template (brand header, link to dashboard) — stay within SES size limits and accessibility basics.
- **Deliverable:** staging inbox review checklist; short doc of known LLM failure modes and mitigations.

**Phase 6.6 closure (2026-06):** `SYSTEM_GUIDELINES` + `build_prompt` extended for partial recovery coverage, null ACWR, and null readiness; `HIGH_SLEEP_DEBT` / `LOW_HRV` gated when the matching 7-day observation count is explicitly zero (legacy rows without coverage columns unchanged). HTML email: `lang="en"`, charset meta, Soma header, optional `BRIEFING_EMAIL_DASHBOARD_URL` footer (http/https only). Docs: [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md), [briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md).

### Phase 7 — More sources (staging-first; no prod second environment)

**Intent:** Keep **one live environment** (staging + your AWS staging stack) while integrations and schema still churn. Avoid operating **staging and prod** in parallel until you deliberately cut over in **Phase 11**.

- **Strava (repo slice) — PAUSED:** `pipeline/adapters/strava.py`, `pipeline/cardio_upsert.py`, `scripts/smoke_strava.py`, Bruno `.bruno/strava/list-athlete-activities.bru` are **in repo**; **live API work, OAuth refresh, and daily-pipeline wiring are deferred** until an **active Strava subscription** (Standard Tier) exists — see [integrations-checklist.md](./integrations-checklist.md) § Strava API access. Until then: **offline tests + fixtures only**. **Multi-user note:** when unpaused, Strava OAuth belongs in **per-user** token storage — Phase 9 **Multi-user rollout — provider connections**.
- **Apple Health (export) — active track:** webhook (e.g. Health Auto Export) → raw → **`biometrics`** / rollup is the **current** Phase 7 vendor priority (parallel agent/session OK).
- Add further sources in order of **dependency / risk** (e.g. Renpho, Google Health before Fitbit sunset) after Apple Health is moving.
- For **every new source**, ship **historical backfill** alongside incremental sync (see **Historical ingestion & backfill** above) so the DB is not “empty until the first cron day.”
- **Deduplication / source priority** as in overview — implement explicitly in code or small config table.
- **Migrations / CDK:** land schema and infra changes on **staging** only in this phase; treat prod promotion, second Supabase project discipline, and “two envs” operations as **out of scope** until Phase 11.

### Phase 8 — Aggregation hardening + anomaly + pattern layers

**Normative spec:** [Signal pipeline: where intelligence lives](#signal-pipeline-where-intelligence-lives). This phase implements **Layers 1–3** (and wires **Layer 4** prompts); it does not change the “one daily pipeline” edge architecture.

**8a — Layer 1 (Postgres aggregates, incremental)**

- Ship targeted SQL (migration + optional **materialized views** or **`daily_signal_snapshots` / `metric_baselines`** upserts) for any rolling metric that is **cheaper or clearer** in the database than in `pipeline/features.py` — start with **read paths** the anomaly job and dashboard will share (e.g. 30d/90d means, DoD/WoW deltas, PR summary table). Keep **`daily_features`** as the compatibility surface unless you deliberately denormalize.
- Optionally enable **Supabase `pg_cron`** for `REFRESH MATERIALIZED VIEW` on a schedule if you want aggregation off the critical path of the main Lambda.
- **Tests:** SQL fixtures or migration tests for window definitions; RLS contract for any new table (`tests/test_migration_rls_contract.py`).

**8b — Layer 2 (deterministic statistics in Lambda)**

- Add **`pipeline/stat_anomalies.py`** (name flexible): load recent `daily_health_metrics` / `daily_features` (+ Layer 1 outputs when present); compute **Z-score**, **IQR fence**, and **EWMA / drift** signals per the signal-pipeline table; respect **minimum observation counts** and reuse Phase 6.6 sparse-data philosophy.
- **Persist** to **`anomaly_events`** with `anomaly_type = 'statistical'` and numeric detail in **`context_json`**; add a **dedupe** story (partial unique index or pre-delete for `(user_id, detected_date, metric, method)`).
- **Orchestration:** extend `DailyPipelineIO` + `run_daily_pipeline` with `load_recent_features_for_stats`, `persist_anomaly_events`, and a **`statistical_anomalies`** step **after rules, before `generate_briefing`**.
- **Lambda layer:** add **NumPy** / **SciPy** (and optionally **statsmodels**) with size discipline; document cold-start / unzip size in `infrastructure/lambda/briefing/README.md` or a sibling doc.
- **Tests:** hermetic unit tests with fixed float series (no network); assert row shapes written to `anomaly_events`.

**8c — Layer 3 (pattern library + optional weekly LLM)**

- Migration: **`metric_patterns`** (or equivalent) with RLS; **weekly** EventBridge rule → Lambda (new handler or shared module) that upserts correlations / lag relationships with caps per user.
- **Optional:** `pgvector` extension + embedding column for **daily summary vectors** (k-NN “similar past days”) — feature-flagged.
- **Weekly Sonnet scan:** optional, behind **`ENABLE_WEEKLY_PATTERN_LLM`** (or similar); writes **`anomaly_type = 'llm_pattern'`** or proposes **`metric_patterns`** rows for review — never the only source of **numeric** outliers.

**8d — Layer 4 wiring (briefing)**

- Update **`pipeline/briefing.build_prompt`** to inject **structured** blocks: today’s metrics subset, **rules flags**, **statistical anomalies** (from DB or in-memory), **trends**, **top-N active patterns** — matching the JSON contract in the signal-pipeline section. Extend [briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md) with “model must not override z-scores / counts.”

**Deliverable:** Staging shows new rows in `anomaly_events` on real pipeline runs; daily email includes a concise **Signals** section grounded in persisted stats; weekly job documented (cron + idempotency). Phase 10 can assume this payload shape when adding guidelines.

### Weekly / monthly workload (training load indicators) — cross-cutting

**Product goal:** Surface a **week**- and **month**-scale view of **total workload** / **training exposure** — from as simple as **total exercise time** (especially cardio minutes) through richer **modality-specific** signals (strength tonnage, hard sets, session counts). “Total stress” framing should stay **honest**: without session HR, avoid marketing a single number as deep **physiological stress** (see [workload-indicators.md](./workload-indicators.md)).

- **Evidence-backed design (v0 vs v1):** [workload-indicators.md](./workload-indicators.md) — modality-split **external** load first; optional **Foster session RPE × duration** when users opt in; HR-TRIMP only when streams exist; keep **ACWR-style** ratios as **spike vs baseline** language, not injury oracle.
- **Repo status:** Migration `0003_training_load_and_effort.sql` + `pipeline/features.py` populate **`training_load_*`** (7d/28d) and **`effort_*`** (unified heuristic index + Foster AU when RPE / `session_rpe` exist). Legacy `cardio_minutes_*` / `strength_tonnage_7d` unchanged for rules/backcompat. **Still optional later:** ISO **calendar week** rollups (today = trailing 7d / 28d only).

### Phase 9 — User app: homepage / dashboard + bounded queries (optional stack)

**Product shape:** Not only an NL-query playground — ship a **homepage / dashboard** the user actually opens: key **daily features** and trends, **latest briefing** (link, excerpt, or light embed), **integration / sync health** (connected sources, last successful pull), **weekly / monthly training load** (modality-split external load first — see [workload-indicators.md](./workload-indicators.md) and the **Weekly / monthly workload** section above), and simple tables or charts where they add clarity. Layer **bounded** natural-language or saved-query exploration on top of that shell (same auth and RLS-backed or read-only DB path), not instead of it.

- **Stack:** Streamlit spike → Next.js PWA (or similar) if validated; any text-to-SQL only with **schema-bound** prompts and a **read-only** role (or equivalent RLS-only client) — threat model in supplement.
- **Multi-user rollout — provider connections:** Today’s pipeline assumes **operator-held** credentials (env vars, Bruno, smoke scripts). **If/when you roll out to additional users**, you need a deliberate way for **each user** to connect their own data sources — not a shared token. Plan product + backend for **per-user auth and secrets** (OAuth flows, refresh tokens, and consent for **Strava**, **Hevy** or equivalent strength APIs, **Apple Health** export/webhooks or HealthKit-backed paths, **Google Health Connect** / Fit, **Renpho**, etc.), plus **sync health** in the dashboard (connected / error / last pull). Until that exists, new users cannot safely onboard without duplicating the operator’s manual wiring.
- **Multi-user rollout check:** Before calling onboarding “done,” walk a **second user** (or clean test account) through the full path: sign-up / invite, profile + **`user_settings` / email** for SES briefings, **self-serve provider connection** (not shared operator credentials), per-user **SSM rules** (or automation that creates `/soma/{env}/{user_id}/rules/…`) if still required, and **confirm the daily pipeline delivers** to that user without one-off manual Lambda edits. Document whatever remains manual; prefer **automation or self-serve** so new users do not depend on the operator wiring notifications by hand. *(If the current design already covers this end-to-end, Phase 9 is the gate to **verify** and close gaps.)*

### Phase 10 — Integrated delivery refinement (guidelines, corpus, operator polish, recurring)

**When:** After **Phase 7** (additional sources + backfill on **staging**) and **Phase 8** (statistical anomalies in the briefing prompt)—so the model already sees **mixed sources** and **anomaly blocks** before you grow prompt context. **Production** and dual-environment operations wait until **Phase 11**. **Phase 6.6** shipped the first quality bar (sparse recovery, HTML shell, checklist + failure-mode docs). Phase 10 adds **personal + expert narrative context** and then runs **ongoing** SES / prompt / template maturation—not a second “Phase 6” scope.

**Training guidelines + expert transcript corpus** (briefing context — **Guidelines Files** and **Prompt Template & LLM Call** in [project-overview.md](./project-overview.md)):

- **Runtime wiring:** Load `my-goals.md` and `expert-principles.md` per user from **S3** (overview path `guidelines/{user_id}/…`) or an agreed alternative (e.g. Supabase Storage); inject into `pipeline/briefing.build_prompt` / `generate_briefing` alongside flags + features + **anomalies** (from Phase 8). IAM for the briefing role; keep prompts bounded (truncate/hash long files if needed).
- **One-time corpus builder (operator / local script):** Curated list of **~12 YouTube URLs** (e.g. Mike Israetel, **Jeremy Ethier**, Jeff Nippard — your picks). For each video: obtain **captions/transcripts** (prefer **official** caption export or **manually pasted** transcript files you own; respect **YouTube Terms of Service** and copyright — do not ship a scraper that violates ToS in automation). Optional: LLM-assisted **condensation** into structured bullets for `expert-principles.md`, then **human review** before upload to S3.
- **Corpus deliverables:** `scripts/` or `pipeline/tools/` README for the one-time flow; sample `expert-principles.md` skeleton; contract tests that the briefing prompt includes injected guideline text when files exist (mocked S3).

**Email, HTML, and prompt engineering** (recurring):

- **Email / HTML:** Re-run [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md) across major clients (Gmail, Apple Mail, Outlook); tune layout, contrast, footer links, and SES **size** limits as templates grow.
- **Prompt engineering:** Iterate `SYSTEM_GUIDELINES` / `build_prompt` on misfires from production-like traffic; extend [briefing-llm-failure-modes.md](./briefing-llm-failure-modes.md); revisit **max_tokens**, model id, and context truncation when guidelines + anomaly blocks grow.
- **Templating / env:** Wire `BRIEFING_EMAIL_DASHBOARD_URL` (and similar) via CDK per env when product URLs stabilize.
- **Polish deliverables:** Short dated notes in `docs/plans/` or PR descriptions per polish cycle—no separate gate unless regressions force one.

### Phase 11 — Production cutover + staging as cheap (or absent) as you want

**When:** After you are willing to own **two** environments (prod + something non-prod) and the product path through **Phase 9** (or your minimum bar for users + notifications) is clear.

- **Prod:** Promote **Supabase migrations** and **CDK** to **production** stacks; secrets, SES identities, and **`/soma/prod/…`** SSM trees per real user; runbooks and optional security pass before first real-user traffic.
- **Staging cost posture:** Goal is **no meaningful AWS or schedule spend** on staging when you are not actively developing integrations. Prefer one of:
  - **Destroy staging stack** (and optionally the staging Supabase project) when idle; **recreate empty** when you need it again **with nothing scheduled** (no EventBridge rules firing, no daily Lambda invocations) until you explicitly turn schedules back on for a test window, **or**
  - **Leave stack deployed** but **disable schedules** / tear down EventBridge targets and scale-to-zero patterns so nothing runs on a timer — whichever matches your tolerance for leftover resources vs full teardown.
- **Explicit non-goal for “cheap staging”:** Do not rely on staging receiving real nightly traffic or holding production-like cost — staging exists for **pre-prod validation**, not parallel operation with prod.

---

## Dependencies

- **AWS:** IAM, S3, Lambda, EventBridge (or Step Functions), SES, Secrets Manager, SSM, CloudWatch. **IaC:** **AWS CDK v2 (Python) only** — no Terraform or SAM for Soma; single-account staging/prod OK via separate CDK stacks/stages.
- **Supabase:** staging + prod projects (or single project + branches if you adopt that model — decide explicitly). **Phase 7–10** assume you can lean on **staging** only; **Phase 11** is when you commit to prod and may **destroy or idle staging** (empty DB, no schedules) to stay cheap.
- **Anthropic:** API keys, spend limits; **model IDs** pinned in `pipeline/briefing.DEFAULT_BRIEFING_MODEL` / `BRIEFING_MODEL` env (refresh when Anthropic retires aliases — see [model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)).
- **External APIs:** Hevy Pro API, Strava OAuth, Health Auto Export behavior, Google Health Connect / OAuth, Renpho, CalDAV.
- **Numerics (Phase 8):** **NumPy / SciPy** (optional statsmodels) in the briefing Lambda layer or a slim sibling layer — watch **250 MB** unzipped deployment package limit; prefer **AWS Lambda layers** split if needed.
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
| **Low** | **Statistical false positives:** aggressive Z/IQR thresholds or short baselines → noisy `anomaly_events` and muddled briefings; tune with SSM or config and minimum observation counts. |

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
| Windowed aggregates, indexes, MV refresh | **supabase-postgres-best-practices** skill + Supabase MCP advisors. |
| SciPy / numpy in Lambda packaging | `aws-lambda` skill; validate layer size and cold start. |
| Lambda, EventBridge, Step Functions | `aws-lambda` / `aws-serverless-deployment` skills; AWS MCP for IaC snippets if enabled. |
| AWS CDK (Python) app layout | **deployment-engineer** / CDK patterns; AWS docs — **Terraform not used** for Soma. |
| GitHub Actions → AWS (OIDC, deploy) | **deployment-engineer** / **deploy-ci-cd-agent** patterns; AWS IAM OIDC trust for `token.actions.githubusercontent.com`. |
| Security review before prod | Dedicated security-review / ce-security-reviewer after auth + SES + secrets land. |
| E2E of email path | Manual smoke: [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md); automated SES integration tests optional later. |

Use **planner → implement** workflow: keep this file updated when phases complete.

---

## Out of Scope (unless you ask)

- **Terraform or AWS SAM** for Soma AWS resources — use **CDK Python** only (keeps one language with `pipeline/`).
- Rewriting `project-overview.md` in place (use supplement for corrections).
- Parquet cold archive / “second query engine” until retention or cost proves necessary (overview itself is mixed on Phase 4 archival — pick one story).
- OpenClaw or always-on agent hosts (already “archived” in overview — aligned).
- Nike Run Club as ongoing integration (historical export only).
- **Managed ML anomaly SaaS** (e.g. SageMaker / Lookout for Metrics), **dedicated TSDBs** (TimescaleDB, InfluxDB), and **LLM-primary numeric anomaly detection** — see [Signal pipeline § Explicitly out of scope](#explicitly-out-of-scope-for-this-pipeline).

---

## Estimated Complexity

**High** for full multi-source + orchestration + RLS-correct batch design — roughly **80–160+ hours** spread across evenings/weekends (depends on OAuth sources and operational polish). **Medium** for a credible **Hevy + local + staging email** vertical slice — roughly **24–40 hours**.

---

## Open Questions (need your input)

See [project-overview-supplement.md](./project-overview-supplement.md) § Questions for product owner.

# Soma

**Soma** is a personal health operating system: it aggregates fitness and health data from multiple sources, stores it in a normalized, queryable database, and runs a daily pipeline that combines deterministic rules, anomaly detection, and an LLM to produce a morning briefing (sleep, recovery, training load, and long-term goals).

## What it does

- **Ingest** from **Hevy** (strength API), **Apple Health hub** (HAE webhook — Watch, Renpho body comp, Google/Fitbit via Health Sync, workouts), **Strava** (paused), and **iCloud Calendar** (CalDAV).
- **Persist raw responses** to object storage before normalization so you can replay and recover from bugs.
- **Normalize** into a user-scoped event store (planned: Supabase PostgreSQL with Row-Level Security).
- **Compute** daily features, run a rules engine and anomaly layer, then **synthesize** coaching copy with an LLM (the model narrates pre-computed signals; it does not replace the logic layer).
- **Deliver** a daily briefing (e.g. email via SES) on a schedule.

The design targets multiple users and isolated staging and production environments. **Local development** is documented without Docker: Bruno for vendor APIs, hosted Supabase for schema and PostgREST — see [docs/plans/local-dev-and-tooling.md](docs/plans/local-dev-and-tooling.md).

## Documentation

Full architecture, data sources, schema direction, and operational notes live in:

**[docs/plans/project-overview.md](docs/plans/project-overview.md)**

Phased implementation plan, orchestration/timing notes, and doc validation:

- **[docs/plans/implementation-plan.md](docs/plans/implementation-plan.md)** — phased build, risks, agents/plugins when coding  
- **[docs/plans/project-overview-supplement.md](docs/plans/project-overview-supplement.md)** — pipeline timing, inconsistencies flagged, open questions  
- **[docs/plans/local-dev-and-tooling.md](docs/plans/local-dev-and-tooling.md)** — no-Docker workflow, Bruno, Supabase REST mapping  
- **[docs/plans/integrations-checklist.md](docs/plans/integrations-checklist.md)** — services to integrate (confirm / edit)  
- **[infrastructure/README.md](infrastructure/README.md)** — CDK app: single `SomaStack`, synth & deploy  

## Status

This repository holds the product and technical plan, the **`pipeline`** Python package (installable via `pyproject.toml`), **planned SQL DDL** (`schema/soma-planned-schema.sql`), schema docs under `docs/schema/`, **AGENTS.md**, Bruno guidance (`.bruno/README.md`), and a minimal **AWS CDK (Python)** app under **`infrastructure/`** (single `SomaStack`). Deployed AWS resources beyond synth still require your account bootstrap + `cdk deploy`.

## Development

**`pyproject.toml`** is the standard Python project manifest: it declares the package name, Python version, optional dev dependencies (`pytest`), and setuptools packaging so `pip install -e .` installs the `pipeline` package in editable mode. You can ignore it day-to-day and just run `pip`/`pytest` yourself if you prefer.

**`Makefile`** is a thin convenience so you do not have to remember venv paths. **`make`** alone runs **tests**; use **`make install`** once to create `.venv` and install deps. Optional — delete the Makefile if you prefer raw commands.

**Python version:** this repo targets **Python 3.14+** (`requires-python` in `pyproject.toml`). Install 3.14 locally (e.g. [pyenv](https://github.com/pyenv/pyenv) or python.org), then:

```bash
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q pipeline
```

Or with Make (`python3.14` by default; override with `PYTHON=…` if needed):

```bash
make install        # one-time: create .venv + pip install -e ".[dev]"
make                # same as `make test` — pytest
make compile        # bytecode compile check for pipeline/
make dashboard      # launch Streamlit dashboard (fixture mode; no DB needed)
make dashboard-live # launch Streamlit dashboard against your Supabase data
make cdk-synth      # pip install .[cdk] + CDK synth (local pip builds Lambda layer; Python 3.14 + PyPI)
```

### Dashboard (Streamlit)

Braindead-simple launch — from the repo root, just run:

```bash
make dashboard
```

That creates `.venv` if it is missing, installs the `dashboard` extra, and opens the app at **http://localhost:8501** in **fixture mode** (bundled sample data — no database, secrets, or `.env` required).

For **live data** from your Supabase project (reads `SOMA_USER_ID` + `SOMA_DATABASE_URL`, or `DB_CONNECT_STRING`, from repo-root `.env`):

```bash
make dashboard-live
```

Prefer raw commands instead of Make? The equivalent is:

```bash
.venv/bin/pip install -e ".[dashboard]"
SOMA_DASHBOARD_FIXTURE=1 .venv/bin/streamlit run dashboard/app.py   # fixture
SOMA_DASHBOARD_FIXTURE=0 .venv/bin/streamlit run dashboard/app.py   # live
```

**Public URL (AWS):** `cdk deploy` provisions an **App Runner** service (`soma-dashboard`)
with a default HTTPS endpoint. The stack output **`DashboardUrl`** is the link to open from
any browser; the briefing Lambda gets the same value as `BRIEFING_EMAIL_DASHBOARD_URL` for
email footers. Fill the **`soma-dashboard`** secret (Supabase URL + anon key + Anthropic key)
and ensure **`soma-db`** has your session-pooler URI. Disable App Runner with
`-c soma:dashboardEnabled=false` if you only want local Streamlit. See
[`infrastructure/README.md`](infrastructure/README.md) and [`scripts/guidelines-corpus.md`](scripts/guidelines-corpus.md).

Copy [`.env.example`](.env.example) to `.env` for local secrets (gitignored). `ENV` defaults to `local`; see `pipeline.settings`. For **Phase 3 Hevy smoke** (live API, raw files on disk, optional Supabase upsert), see [`scripts/README.md`](scripts/README.md) and [docs/plans/local-dev-and-tooling.md](docs/plans/local-dev-and-tooling.md) § Phase 3 smoke.

### AWS CDK (Python)

Infra code lives in [`infrastructure/`](infrastructure/). Single stack: **`SomaStack`** (CloudFormation id `SomaStagingStack`, kept for in-place updates — see [infrastructure/README.md](infrastructure/README.md)).

```bash
pip install -e ".[cdk]"          # aws-cdk-lib + constructs (from repo root)
make cdk-synth                   # writes infrastructure/cdk.out/ (uses npx aws-cdk CLI)
```

**`cdk synth`** builds the briefing Lambda dependency layer with **local** ``pip`` (no Docker): use **Python 3.14** and allow PyPI access. On Apple Silicon the layer targets **x86_64** Lambda wheels.

See [`infrastructure/README.md`](infrastructure/README.md) for bootstrap and deploy commands.


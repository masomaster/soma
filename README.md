# Soma

**Soma** is a personal health operating system: it aggregates fitness and health data from multiple sources, stores it in a normalized, queryable database, and runs a daily pipeline that combines deterministic rules, anomaly detection, and an LLM to produce a morning briefing (sleep, recovery, training load, and long-term goals).

## What it does

- **Ingest** from sources such as Hevy, Apple Health (via webhooks), Strava, Google Health, Renpho, and iCloud Calendar (read-only).
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
- **[infrastructure/README.md](infrastructure/README.md)** — CDK app: `SomaStagingStack` / `SomaProdStack`, synth & deploy  

## Status

This repository holds the product and technical plan, the **`pipeline`** Python package (installable via `pyproject.toml`), **planned SQL DDL** (`schema/soma-planned-schema.sql`), schema docs under `docs/schema/`, **AGENTS.md**, Bruno guidance (`.bruno/README.md`), and a minimal **AWS CDK (Python)** app under **`infrastructure/`** (`SomaStagingStack`, `SomaProdStack`). Deployed AWS resources beyond synth still require your account bootstrap + `cdk deploy`.

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
make install    # one-time: create .venv + pip install -e ".[dev]"
make            # same as `make test` — pytest
make compile    # bytecode compile check for pipeline/
make cdk-synth  # pip install .[cdk] + CDK synth (writes cdk.out under infrastructure/)
```

Copy [`.env.example`](.env.example) to `.env` for local secrets (gitignored). `ENV` defaults to `local`; see `pipeline.settings`. For **Phase 3 Hevy smoke** (live API, raw files on disk, optional Supabase upsert), see [`scripts/README.md`](scripts/README.md) and [docs/plans/local-dev-and-tooling.md](docs/plans/local-dev-and-tooling.md) § Phase 3 smoke.

### AWS CDK (Python)

Infra code lives in [`infrastructure/`](infrastructure/). Stable stack names: **`SomaStagingStack`**, **`SomaProdStack`**.

```bash
pip install -e ".[cdk]"          # aws-cdk-lib + constructs (from repo root)
make cdk-synth                   # writes infrastructure/cdk.out/ (uses npx aws-cdk CLI)
```

Direct `python infrastructure/app.py` also runs `app.synth()` but emits assembly to a **temp** dir unless you use the CDK CLI — prefer `make cdk-synth` or `cd infrastructure && cdk synth SomaStagingStack`.

See [`infrastructure/README.md`](infrastructure/README.md) for bootstrap and deploy commands.


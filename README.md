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
- **[docs/schema/README.md](docs/schema/README.md)** — planned SQL schema (diagram + full DDL)

## Status

This repository holds the product and technical plan, the **`pipeline`** Python package (installable via `pyproject.toml`), **planned SQL DDL** (`schema/soma-planned-schema.sql`), schema docs under `docs/schema/`, **AGENTS.md**, and Bruno guidance (`.bruno/README.md`). **AWS CDK (Python)** app and deployed AWS resources are not in this repo yet.

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
```

Copy [`.env.example`](.env.example) to `.env` for local secrets (gitignored). `ENV` defaults to `local`; see `pipeline.settings`.


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

This repository holds the product and technical plan, the **`pipeline`** Python package (installable via `pyproject.toml`), **planned SQL DDL** (`schema/soma-planned-schema.sql`), schema docs under `docs/schema/`, **AGENTS.md**, and Bruno guidance (`.bruno/README.md`). Terraform and AWS resources are not in this repo yet.

## Development

```bash
make install    # creates .venv and pip install -e ".[dev]"
make test       # pytest
make compile    # bytecode compile check for pipeline/
```

Copy [`.env.example`](.env.example) to `.env` for local secrets (gitignored). `ENV` defaults to `local`; see `pipeline.settings`.


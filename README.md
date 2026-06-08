# Soma

**Soma** is a personal health operating system: it aggregates fitness and health data from multiple sources, stores it in a normalized, queryable database, and runs a daily pipeline that combines deterministic rules, anomaly detection, and an LLM to produce a morning briefing (sleep, recovery, training load, and long-term goals).

## What it does

- **Ingest** from sources such as Hevy, Apple Health (via webhooks), Strava, Google Health, Renpho, and iCloud Calendar (read-only).
- **Persist raw responses** to object storage before normalization so you can replay and recover from bugs.
- **Normalize** into a user-scoped event store (planned: Supabase PostgreSQL with Row-Level Security).
- **Compute** daily features, run a rules engine and anomaly layer, then **synthesize** coaching copy with an LLM (the model narrates pre-computed signals; it does not replace the logic layer).
- **Deliver** a daily briefing (e.g. email via SES) on a schedule.

The design targets multiple users, isolated staging and production environments, and local development with Docker Postgres and LocalStack where possible.

## Documentation

Full architecture, data sources, schema direction, and operational notes live in:

**[docs/plans/project-overview.md](docs/plans/project-overview.md)**

## Status

This repository currently holds the product and technical plan. Application code and infrastructure are not present yet; use the overview doc as the source of truth for intended stack (Python pipeline, AWS, Supabase Auth, etc.) and conventions.

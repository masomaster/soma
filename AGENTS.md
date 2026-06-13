# Agent / plugin guidance for Soma

Use this when choosing **skills**, **subagents**, or **MCP** tools so work stays consistent with [`.cursor/rules/soma.mdc`](.cursor/rules/soma.mdc).

## Change style

This repo is **greenfield**: improve structure, names, and boundaries as you learn — not “smallest diff at all costs.” Keep PRs reviewable (avoid mixing unrelated refactors with risky behavior changes in one go).

| Task type | Prefer |
|-----------|--------|
| Supabase schema, RLS, migrations, advisors | **Supabase** skill; **Supabase MCP** (`search_docs`, project read tools, `execute_sql` where appropriate) for staging inspection and doc-backed answers — **avoid blind `apply_migration` on prod**. Prefer MCP over guessing when the agent has Supabase MCP enabled. |
| Postgres query shape, indexes | **supabase-postgres-best-practices** skill. |
| Lambda handlers, EventBridge, Step Functions | **aws-lambda** / **aws-serverless-deployment** skills. |
| AWS CDK (Python) stacks / stages | **deployment-engineer** / AWS CDK docs; deploy-on-aws plugin if used for design review — **no Terraform** in Soma. |
| GitHub Actions, OIDC → AWS, deploy workflows | **deployment-engineer** / **deploy-ci-cd-agent**; AWS IAM OIDC provider for GitHub. |
| After auth, webhooks, secrets, or SES | **security-review** (or equivalent) pass before prod. |

**Phase 0 scope:** Python package + tests + docs only — no cloud provisioning in this repo step.

## Cursor Cloud specific instructions

Soma is a Python library (the `pipeline` package) — there is **no long-running server or UI**. "Running the app" means importing/exercising the pipeline modules or running the test suite; the daily pipeline is composed of Lambda-bound functions that are not wired into a process yet.

- **Python 3.14 is required** (`requires-python = ">=3.14"`). Ubuntu's apt has no 3.14, so it is provided via a `uv`-managed CPython exposed on `PATH` as `python3.14` (symlinked into `/usr/local/bin`). The startup/update script (re)creates `.venv` from that interpreter, so the documented `make`/`python3.14` commands work as-is.
- Standard commands live in the `Makefile`/`README.md`: `make` (pytest), `make compile` (bytecode check), `make cdk-synth` (optional infra synth). Use `.venv/bin/python` directly if you prefer.
- Tests are **hermetic/offline** — HTTP (`urllib`) and `psycopg2` are monkeypatched and fixtures are local, so no Supabase/AWS/Anthropic connectivity is needed to run `pytest`.
- `make cdk-synth` is optional; it installs the `.[cdk]` extra and shells out to `npx aws-cdk@2` (Node is already present). It only synthesizes templates — it does not touch any AWS account.
- `ENV` defaults to `local` (see `pipeline.settings`); in `local` the design prints to stdout instead of sending email. No `.env` is needed just to run tests.

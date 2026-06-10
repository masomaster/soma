# Agent / plugin guidance for Soma

Use this when choosing **skills**, **subagents**, or **MCP** tools so work stays consistent with [`.cursor/rules/soma.mdc`](.cursor/rules/soma.mdc).

| Task type | Prefer |
|-----------|--------|
| Supabase schema, RLS, migrations, advisors | **Supabase** skill; **Supabase MCP** for staging inspection (avoid blind `apply_migration` on prod). |
| Postgres query shape, indexes | **supabase-postgres-best-practices** skill. |
| Lambda handlers, EventBridge, Step Functions | **aws-lambda** / **aws-serverless-deployment** skills. |
| Terraform / AWS layout | **terraform-specialist** or deploy-on-aws plugin patterns. |
| After auth, webhooks, secrets, or SES | **security-review** (or equivalent) pass before prod. |

**Phase 0 scope:** Python package + tests + docs only — no cloud provisioning in this repo step.

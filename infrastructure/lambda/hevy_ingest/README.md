# Hevy scheduled ingest Lambda asset

Thin `handler.py` runs :func:`pipeline.hevy_scheduled_ingest.run_hevy_scheduled_ingest`:
paginate Hevy `GET /v1/workouts`, write each page to **S3** under ``raw/{user_id}/hevy/...``,
then upsert **``strength_events``** (same pattern as ``scripts/smoke_hevy.py``).

## Packaging (CDK)

Same **Lambda layer** as the briefing and Apple webhook functions
(``soma_cdk.pipeline_layer.build_pipeline_deps_layer``). Runtime **Python 3.14 on x86_64**.
``boto3`` comes from the Lambda runtime.

## Configuration

| Var | Purpose |
|-----|---------|
| `ENV` | `staging` / `prod` (set by CDK) |
| `RAW_BUCKET` | S3 bucket name (shared with Apple Health raw ingest) |
| `SOMA_DB_SECRET_ARN` | Postgres URI (plain secret) |
| `SOMA_HEVY_SECRET_ARN` | Hevy Pro API key (plain secret) |
| `SOMA_TENANT_SECRET_ARN` | Supabase `auth.users.id` UUID (plain secret) |

## Secrets

**``DB_CONNECT_STRING``**, **``HEVY_API_KEY``**, and **``SOMA_USER_ID``** are read via
:func:`pipeline.lambda_secrets.resolve_db_connect_string`,
:func:`pipeline.lambda_secrets.resolve_hevy_api_key`, and
:func:`pipeline.lambda_secrets.resolve_soma_user_id` — either plain env vars or the
matching ``soma-db``, ``soma-hevy``, and ``soma-tenant`` secrets.

Placeholder ``update_me`` is rejected for the Hevy key and user id until you replace
them in Secrets Manager (same seed parameter pattern as
[`../briefing/README.md`](../briefing/README.md)).

## Historical backfill

Scheduled ingest only adds **new** workouts after deploy. For full Hevy history, run once from your laptop:

```bash
python scripts/smoke_hevy.py backfill
```

Confirm or troubleshoot: [docs/plans/staging-validation-checklist.md](../../../docs/plans/staging-validation-checklist.md) § Hevy backfill.

## Schedule

CDK creates an EventBridge **Scheduler** schedule named ``soma-{env}-hevy-ingest`` at **09:00 UTC** by default, before the daily briefing schedule (11:00 UTC) so ``strength_events`` is current for features.

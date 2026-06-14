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
| `SOMA_LAMBDA_SECRET_ARN` | Secrets Manager JSON (see below) |

## Secrets

**``DB_CONNECT_STRING``**, **``HEVY_API_KEY``**, and **``SOMA_USER_ID``** (Supabase
``auth.users.id`` for the tenant) are read via :func:`pipeline.lambda_secrets.resolve_db_connect_string`,
:func:`pipeline.lambda_secrets.resolve_hevy_api_key`, and
:func:`pipeline.lambda_secrets.resolve_soma_user_id` — either plain env vars or keys
on the same ``soma-{env}-lambda-runtime`` secret as the briefing Lambda.

Placeholder ``update_me`` is rejected for the Hevy key and user id until you replace
them in Secrets Manager (same seed parameter pattern as
[`../briefing/README.md`](../briefing/README.md)).

## Schedule

CDK creates an EventBridge **Scheduler** schedule named ``soma-{env}-hevy-ingest`` at **09:00 UTC** by default, before the daily briefing schedule (11:00 UTC) so ``strength_events`` is current for features.

# Apple Health webhook Lambda

Thin handler: validate optional shared secret → parse JSON → **S3 raw** → normalize
**biometrics** + **cardio_events** → Postgres upserts (service role via
`DB_CONNECT_STRING`).

Bundled with the same **pipeline Lambda layer** as the daily briefing function
(`pipeline` + `psycopg2-binary`). `boto3` is provided by the Lambda runtime.

## After `cdk deploy`

1. **Copy the ingest URL** from CloudFormation **Outputs** (key `AppleHealthIngestUrl`) — it ends with `/ingest/apple-health`.

2. In **AWS Lambda** console → `soma-{staging|prod}-apple-health-webhook` → *Configuration* → *Environment variables* (optional override):
   - **`APPLE_HEALTH_WEBHOOK_SECRET`** — if set here (non-`update_me`), it overrides the JSON key below. Usually leave unset and use Secrets Manager only.

3. **Secrets Manager** — secret **`soma-{env}-lambda-runtime`** (same JSON as DB / Anthropic / SES):

   - **`DB_CONNECT_STRING`** must be set (this Lambda does not need Anthropic or SES at runtime).
   - **`APPLE_HEALTH_WEBHOOK_SECRET`** — set to a long random string to require header **`X-Soma-Webhook-Secret`**. The placeholder **`update_me`** (from a fresh seed deploy) is treated as **unset** (webhook open) until you replace it. The Apple Health Lambda already has **`secretsmanager:GetSecretValue`** on this secret.

4. In **Health Auto Export** (iOS): create a **REST API** / webhook automation:
   - **URL:** the output URL above.  
   - **Method:** POST, **JSON** body.  
   - **Headers:** add `X-Soma-User-Id: <your Supabase auth user UUID>` (same as `SOMA_USER_ID` in local smoke). If you set a webhook secret, add `X-Soma-Webhook-Secret: ...`.

5. **Enable workouts + metrics** in the HAE export so `data.workouts` and `data.metrics` are populated (see `docs/plans/apple-health-export.md`).

## Hevy overlap (strength)

Before upserting **`cardio_events`**, the handler drops **Apple Health** workouts whose activity type is strength-like (**Traditional / Functional / Core training**) when **`strength_events`** already has **`source = hevy`** rows on the **same calendar day**. See `pipeline/apple_hevy_cardio_dedup.py`. Successful responses include **`cardio_events_dropped_hevy_strength_dup`**.

## API Gateway access logs

After deploy, open CloudWatch → Log groups → **`/aws/apigateway/soma-{env}-apple-health-access`** (CloudFormation output **`AppleHealthHttpApiAccessLogGroup`**). Every request to the public HTTP API URL is logged (method, route, status, integration status/latency, request id). Use this to confirm Health Auto Export is reaching AWS before debugging the Lambda log group.

## Raw objects

JSON is stored under `s3://{RAW_BUCKET}/raw/{user_id}/apple_health_export/{YYYY-MM-DD}/{timestamp}.json`.

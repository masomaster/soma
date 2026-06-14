# Briefing Lambda asset

`handler.py` is the thin entry point for the daily briefing pipeline. It imports
the `pipeline` package and runs `pipeline.orchestration.run_daily_pipeline` for
each active user (see `docs/plans/implementation-plan.md` Phases 5–6).

## Packaging (CDK)

The **pipeline** package and **psycopg2-binary** are built into a **Lambda layer**
by `soma_cdk.pipeline_layer.build_pipeline_deps_layer` using **local `pip`**
(no Docker). The function runtime is **Python 3.14 on x86_64**. This asset
directory contains **only** `handler.py`.

`boto3` is provided by the AWS Lambda Python runtime.

## Secrets (Secrets Manager)

CDK creates a secret named `soma-{staging|prod}-lambda-runtime` whose value is a
JSON object:

```json
{
  "DB_CONNECT_STRING": "update_me",
  "ANTHROPIC_API_KEY": "update_me",
  "SES_SENDER": "update_me"
}
```

The Lambda receives `SOMA_LAMBDA_SECRET_ARN` and calls `GetSecretValue`.

### Not overwriting your real values after you edit in the console

Each stack has a CloudFormation parameter (e.g. **`StagingSeedLambdaRuntimeSecret`**
for staging, **`ProdSeedLambdaRuntimeSecret`** for prod)
with allowed values **`Yes`** / **`No`** (default **Yes** for first deploy).

1. First `cdk deploy`: **Yes** → CloudFormation may set the secret string to the
   `update_me` JSON.
2. Replace the JSON in the **Secrets Manager** console with real values.
3. Deploy again with **No** (CLI:  
   `cdk deploy SomaStagingStack --parameters StagingSeedLambdaRuntimeSecret=No`  
   or set the parameter in the CloudFormation console). With **No**, the template
   passes `AWS::NoValue` for `SecretString` on update so CloudFormation should **not**
   push a new string—your console values stay.

If you leave **Yes** forever, a future template change that still embeds the
placeholder could reset the secret—switch to **No** once the secret is real.

### Local / overrides

If `DB_CONNECT_STRING`, `ANTHROPIC_API_KEY`, and `SES_SENDER` are all set as
ordinary environment variables, those are used instead of Secrets Manager.
See `pipeline.lambda_secrets.resolve_lambda_secrets`.

## Other configuration

| Var | Purpose |
|-----|---------|
| `ENV` | `staging` / `prod` (set by CDK) |
| `SOMA_RULES_PREFIX` | SSM tree for thresholds, set by CDK (`/soma/{env}/`) |
| `BRIEFING_MODEL` | optional; default `claude-haiku-4-5-20251001` (see `pipeline/briefing.py`) |

IAM for SSM rule reads, Secrets Manager read on that secret, and SES send is
granted by `DailyBriefingPipeline`.

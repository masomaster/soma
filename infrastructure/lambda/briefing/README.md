# Briefing Lambda asset

`handler.py` is the thin entry point for the daily briefing pipeline. It imports
the `pipeline` package and runs `pipeline.orchestration.run_daily_pipeline` for
each active user (see `docs/plans/implementation-plan.md` Phases 5–6).

The handler upserts **`daily_health_metrics`**, **`daily_features`**, and
**`daily_briefings`**, and replaces **`anomaly_events`** rows with
`anomaly_type = 'statistical'` for that user/day (delete prior statistical rows,
then insert the current z-score flags — see `pipeline.persistence.replace_statistical_anomaly_events`).

## Packaging (CDK)

The **pipeline** package and **psycopg2-binary** are built into a **Lambda layer**
by `soma_cdk.pipeline_layer.build_pipeline_deps_layer` using **local `pip`**
(no Docker). The function runtime is **Python 3.14 on x86_64**. This asset
directory contains **only** `handler.py`.

`boto3` is provided by the AWS Lambda Python runtime.

## Secrets (Secrets Manager)

CDK creates **per-concern** secrets (see `soma_cdk.runtime_secrets.RuntimeSecrets`):

| Secret name | Format | Keys / value |
|-------------|--------|--------------|
| `soma-db` | plain string | Postgres URI |
| `soma-briefing` | JSON | `ANTHROPIC_API_KEY`, `SES_SENDER` |

The Lambda receives `SOMA_DB_SECRET_ARN` and `SOMA_BRIEFING_SECRET_ARN` and calls
`GetSecretValue` on each (see `pipeline.lambda_secrets.resolve_lambda_secrets`).

### Not overwriting your real values after you edit in the console

The stack has a CloudFormation parameter **`SeedRuntimeSecrets`**
with allowed values **`Yes`** / **`No`** (default **Yes** for first deploy). The
single stack creates and owns the `soma-*` secrets (`RETAIN` policy).

1. First `cdk deploy`: **Yes** → CloudFormation may set placeholder secret strings.
2. Replace values in the **Secrets Manager** console.
3. Deploy again with **No** (CLI:  
   `cdk deploy --all --parameters SeedRuntimeSecrets=No`  
   or set the parameter in the CloudFormation console). With **No**, the template
   passes `AWS::NoValue` for `SecretString` on update so CloudFormation should **not**
   push new strings—your console values stay.

If you leave **Yes** forever, a future template change that still embeds placeholders
could reset secrets—switch to **No** once values are real.

### Migrating from `soma-{env}-lambda-runtime`

If you previously deployed the monolithic secret, copy fields into the new secrets
before switching Lambdas to the new ARNs:

| Old JSON key | New secret |
|--------------|------------|
| `DB_CONNECT_STRING` | `soma-db` (plain) |
| `ANTHROPIC_API_KEY`, `SES_SENDER` | `soma-briefing` (JSON) |

Deploy with **`SeedRuntimeSecrets=No`** after copying so placeholders are not applied.

### Local / overrides

If `DB_CONNECT_STRING`, `ANTHROPIC_API_KEY`, and `SES_SENDER` are all set as
ordinary environment variables, those are used instead of Secrets Manager.
See `pipeline.lambda_secrets.resolve_lambda_secrets`.

## Other configuration

| Var | Purpose |
|-----|---------|
| `ENV` | `local` / `cloud` (set by CDK to `cloud`) |
| `BRIEFING_MODEL` | optional; default `claude-haiku-4-5-20251001` (see `pipeline/briefing.py`) |

Per-user thresholds are read from SSM under `/soma/{user_id}/rules/` (see
`pipeline.rules.rules_ssm_prefix`).

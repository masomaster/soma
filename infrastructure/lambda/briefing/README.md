# Briefing Lambda asset

`handler.py` is the thin entry point for the daily briefing pipeline. It imports
the `pipeline` package and runs `pipeline.orchestration.run_daily_pipeline` for
each active user (see `docs/plans/implementation-plan.md` Phases 5–6).

## What's in this asset vs. provided at deploy

- **In this asset:** `handler.py` only.
- **Provided via a Lambda layer / container image (deploy-time packaging):**
  - the `pipeline` package (this repo)
  - `psycopg2` built for the Lambda platform
  - `boto3` is already present in the AWS-managed Python runtime

`cdk synth` does **not** require the layer, so CI stays Docker-free. Building the
layer (e.g. `pip install . -t layer/python` on a Lambda-compatible image, or a
container image function) is the packaging step tracked for the Phase 6 staging
rollout / Phase 7.

## Required configuration (Lambda env vars / secrets)

| Var | Purpose |
|-----|---------|
| `ENV` | `staging` / `prod` (set by CDK) |
| `SOMA_RULES_PREFIX` | SSM tree for thresholds, set by CDK |
| `DB_CONNECT_STRING` | Supabase **service-role** Postgres connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SES_SENDER` | verified SES From address |
| `BRIEFING_MODEL` | optional model id override |

Store secrets in AWS Secrets Manager / SSM SecureString and inject them; do not
commit them. IAM for SSM rule reads + SES send is granted by `DailyBriefingPipeline`.

# Soma — AWS CDK (Python)

Stable stack IDs (use these in docs, GitHub Actions, and CLI):

| Stack construct id | Purpose |
|--------------------|---------|
| **`SomaStagingStack`** | Staging Lambdas, buckets, rules, … |
| **`SomaProdStack`** | Production |

**Apple Health ingest:** each stack also deploys an **HTTP API** (`POST …/ingest/apple-health`), **access logs** in CloudWatch (`AppleHealthHttpApiAccessLogGroup` output → `/aws/apigateway/soma-{env}-apple-health-access`), an **S3 raw bucket**, and Lambda `soma-{env}-apple-health-webhook` (see [apple-health-export.md](../docs/plans/apple-health-export.md) and `infrastructure/lambda/apple_health_webhook/README.md`). CloudFormation output **`AppleHealthIngestUrl`** is the URL for Health Auto Export.

**Hevy scheduled ingest:** EventBridge **Scheduler** schedule `soma-{env}-hevy-ingest` (default **09:00 UTC**) invokes Lambda `soma-{env}-hevy-ingest`, which writes raw pages to the **same** S3 bucket as Apple (`RAW_BUCKET`) and upserts **`strength_events`**. Set **`HEVY_API_KEY`** and **`SOMA_USER_ID`** in Secrets Manager `soma-{env}-lambda-runtime` (or as Lambda env overrides). See `infrastructure/lambda/hevy_ingest/README.md`.

## Prereqs

- Python **3.14+** (same as repo `pyproject.toml`)
- From repo root: `pip install -e ".[cdk]"` **or** `pip install -r infrastructure/requirements.txt` inside a venv
- [AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html#getting_started_install) (`npm install -g aws-cdk` / `brew install aws-cdk`) **or** use repo root **`make cdk-synth`** (uses `npx aws-cdk@2`, no global install).

## Synth (no AWS call)

`cdk synth` / `cdk deploy` runs **local** ``pip`` to build the briefing Lambda **layer**
(this repo’s ``pipeline`` package plus ``psycopg2-binary``). No Docker. You need
**Python 3.14** on ``PATH`` (same as the Lambda runtime) and network access to PyPI.
On **Apple Silicon**, the bundler requests **manylinux x86_64** wheels so they match
the **x86_64** Lambda architecture.

`python app.py` runs `app.synth()` but by default writes the assembly to a **temp** directory. For **`cdk.out/`** next to the active `cdk.json`, use the CDK CLI or Make:

```bash
# From repo root (recommended)
make cdk-synth

# Or from repo root (uses repo-root `cdk.json`; activate the venv that has `.[cdk]` installed)
cdk synth SomaStagingStack SomaProdStack
cdk diff SomaStagingStack

# Or from infrastructure/ (uses infrastructure/cdk.json)
cd infrastructure
cdk synth SomaStagingStack
cdk synth SomaProdStack
```

## Deploy (needs bootstrapped account/region)

```bash
export CDK_DEFAULT_ACCOUNT=123456789012
export CDK_DEFAULT_REGION=us-west-2
cd infrastructure
cdk bootstrap aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}
cdk deploy SomaStagingStack
# prod: use GitHub Environment + approval; then:
# cdk deploy SomaProdStack
```

**Rule → Scheduler migration:** CloudFormation cannot change an existing resource’s **type** in place (for example `AWS::Events::Rule` → `AWS::Scheduler::Schedule` under the same logical ID). The CDK construct ids for the schedules are chosen so synth produces **new** logical IDs; a normal `cdk deploy` then **deletes** the old rules and **creates** the schedules in one pass.

If you previously deployed a failed hybrid template, fix drift (remove orphaned rules or failed stacks) and deploy again.

Stacks define the **daily briefing** EventBridge **Scheduler** → Lambda pipeline. Runtime secrets
live in Secrets Manager (`soma-{env}-lambda-runtime`); see
`infrastructure/lambda/briefing/README.md` for the seed parameter and how to avoid
overwrites after you edit the secret in the console.

## Pipeline alarms (operator email)

Each stack creates an SNS topic `soma-{staging|prod}-daily-pipeline-alarms` and
CloudWatch alarms that publish to it (briefing + Hevy ingest when wired):

| Alarm | What it catches |
|-------|-----------------|
| `soma-{env}-daily-pipeline-scheduler-target-errors` | EventBridge **Scheduler** ``TargetErrorCount`` for schedule ``soma-{env}-daily-pipeline`` (Lambda returned an error after invoke). |
| `soma-{env}-daily-pipeline-scheduler-invocations-dropped` | Scheduler **gave up** after retries (**InvocationDroppedCount**) — permissions, DLQ, or target misconfiguration. |
| `soma-{env}-daily-briefing-lambda-errors` | Lambda **Errors** (unhandled exception, timeout, etc.). |
| `soma-{env}-daily-briefing-lambda-throttles` | Lambda **Throttles**. |
| `soma-{env}-daily-briefing-user-pipeline-failures` | Log lines matching the per-user catch in `handler.py` (`Daily pipeline failed for user`). |
| `soma-{env}-hevy-ingest-scheduler-target-errors` | Hevy schedule: Scheduler ``TargetErrorCount``. |
| `soma-{env}-hevy-ingest-scheduler-invocations-dropped` | Hevy schedule: **InvocationDroppedCount**. |
| `soma-{env}-hevy-ingest-lambda-errors` | Hevy ingest Lambda **Errors**. |

**Subscribe your inbox** by passing CDK context at synth/deploy (same value for both stacks if you deploy together):

```bash
cdk deploy SomaStagingStack -c soma:pipelineAlarmEmail=you@example.com
```

AWS sends a **subscription confirmation** email; you must click **Confirm** before
alarms are delivered.

If you omit `soma:pipelineAlarmEmail`, the topic is still created so you can add
subscriptions manually (SMS, Slack via Chatbot, another email, etc.).

**Log group note:** The Lambda uses `log_retention` so CDK owns the CloudWatch log
group for metric filters. If deploy fails with “log group already exists” (you
ran the function before this change), delete the **empty** auto-created
`/aws/lambda/soma-{env}-daily-briefing` log group in the console, then redeploy once.

## Continuous deployment (GitHub Actions → AWS)

CI and deploys are wired via GitHub Actions using **OIDC → AWS IAM role** (no stored keys):
`ci.yml` (tests + synth), `deploy-staging.yml` (push to `main`), `deploy-prod.yml` (tag/dispatch + approval).
One-time AWS/GitHub setup and required environment variables are documented in
[`docs/plans/ci-aws.md`](../docs/plans/ci-aws.md).

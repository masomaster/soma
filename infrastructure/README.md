# Soma — AWS CDK (Python)

Soma is single-user with **one deployed environment** (no staging/prod split).
The app registers a single `SomaStack`.

| Stack | CloudFormation id | Purpose |
|-------|-------------------|---------|
| **`SomaStack`** (class) | **`SomaStagingStack`** | All Lambdas, buckets, schedules, secrets, alarms |

> **Why the id is `SomaStagingStack`:** that is the name of the environment that
> is already deployed. Keeping it lets `cdk deploy` **update in place** — preserving
> the live Apple Health API URL, the retained raw S3 bucket, and the `soma-*`
> secrets (including the Supabase `soma-db` connection). The stack has
> **termination protection** enabled and the raw bucket + secrets use `RETAIN`, so
> an accidental delete cannot wipe ingested data or the DB secret. To adopt a clean
> `SomaStack` id later, deploy the renamed stack, re-point the Apple Health webhook
> URL, then delete the old stack (its retained bucket/secrets survive).

All resources use **un-suffixed** `soma-*` names.

**Apple Health ingest:** an **HTTP API** (`POST …/ingest/apple-health`), **access logs** in CloudWatch (`AppleHealthHttpApiAccessLogGroup` output → `/aws/apigateway/soma-apple-health-access`), an **S3 raw bucket**, and Lambda `soma-apple-health-webhook`. CloudFormation output **`AppleHealthIngestUrl`** is the URL for Health Auto Export. All HealthKit data — Watch, **Renpho** body comp, **Google/Fitbit via Health Sync**, mirrored workouts — uses this **one** endpoint. See [apple-health-export.md](../docs/plans/apple-health-export.md).

**Hevy scheduled ingest:** EventBridge **Scheduler** `soma-hevy-ingest` (default **09:00 UTC**) invokes Lambda `soma-hevy-ingest`, writing raw pages to the **same** S3 bucket as Apple (`RAW_BUCKET`) and upserting **`strength_events`**. Secrets: **`soma-db`**, **`soma-hevy`**, **`soma-tenant`**. **Backfill:** [staging-validation-checklist.md](../docs/plans/staging-validation-checklist.md) § Hevy backfill — `python scripts/smoke_hevy.py backfill`. See `infrastructure/lambda/hevy_ingest/README.md`.

**CalDAV scheduled ingest:** Scheduler `soma-caldav-ingest` (**08:00 UTC**) → Lambda `soma-caldav-ingest` → **`interventions`** (`calendar_busy`). Secrets: **`soma-caldav`**, **`soma-db`**, **`soma-tenant`**. `caldav` is bundled in the shared Lambda layer (`pipeline_layer.py`).

**Wahoo FIT (Dropbox API):** EventBridge **Scheduler** `soma-wahoo-fit-ingest` (**08:30 UTC**) → Lambda `soma-wahoo-fit-ingest` → Dropbox list/download → **`cardio_events`** (`wahoo_fit`) + FTP. Secret **`soma-dropbox`** (JSON). `fitdecode` is in the shared Lambda layer. No Mac required — see [cycling-power-ftp.md](../docs/plans/cycling-power-ftp.md).

**Strava scheduled ingest:** Lambda `soma-strava-ingest` deployed; **no Scheduler** until the Strava API subscription is active (`schedule_enabled=False`). Secret **`soma-strava`** when unpaused.

**Weekly signal job:** Scheduler `soma-weekly-signal` (**Sunday 12:00 UTC**) recomputes **`metric_patterns`** and optional Sonnet **`llm_pattern`** rows (`ENABLE_WEEKLY_PATTERN_LLM` on the weekly Lambda only).

**Dashboard (not in this stack):** the Streamlit UI deploys separately on **[Streamlit Community Cloud](https://streamlit.io/cloud)** (free). Pass the public URL to CDK with `-c soma:dashboardUrl=https://….streamlit.app` or GitHub variable **`SOMA_DASHBOARD_URL`** — see [`docs/plans/dashboard-hosting.md`](../docs/plans/dashboard-hosting.md).

## Prereqs

- Python **3.14+** (same as repo `pyproject.toml`)
- From repo root: `pip install -e ".[cdk]"` **or** `pip install -r infrastructure/requirements.txt` inside a venv
- [AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html#getting_started_install) (`npm install -g aws-cdk` / `brew install aws-cdk`) **or** use repo root **`make cdk-synth`** (uses `npx aws-cdk@2`, no global install).

## Synth (no AWS call)

`cdk synth` / `cdk deploy` runs **local** `pip` to build the briefing Lambda **layer**
(this repo's `pipeline` package plus `psycopg2-binary`). No Docker. You need
**Python 3.14** on `PATH` (same as the Lambda runtime) and network access to PyPI.
On **Apple Silicon**, the bundler requests **manylinux x86_64** wheels so they match
the **x86_64** Lambda architecture.

```bash
# From repo root (recommended)
make cdk-synth

# Or from infrastructure/ (uses infrastructure/cdk.json)
cd infrastructure
cdk synth --all
cdk diff --all
```

## Deploy (needs bootstrapped account/region)

```bash
export CDK_DEFAULT_ACCOUNT=123456789012
export CDK_DEFAULT_REGION=us-west-2
cd infrastructure
cdk bootstrap aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}
cdk deploy --all
```

**Rule → Scheduler migration:** CloudFormation cannot change an existing resource's **type** in place (for example `AWS::Events::Rule` → `AWS::Scheduler::Schedule` under the same logical ID). The CDK construct ids for the schedules are chosen so synth produces **new** logical IDs; a normal `cdk deploy` then **deletes** the old rules and **creates** the schedules in one pass.

Runtime secrets live in **per-concern** Secrets Manager resources (`soma-db`, `soma-briefing`, …);
see `infrastructure/lambda/briefing/README.md` for the seed parameter and how to avoid
overwrites after you edit secrets in the console. The stack **creates and owns** these
secrets with a `RETAIN` policy.

## Pipeline alarms (operator email)

The stack creates an SNS topic `soma-daily-pipeline-alarms` and CloudWatch alarms
that publish to it on **ALARM** and **OK** transitions. When
`soma:pipelineAlarmEmail` is set, a small Lambda (`soma-pipeline-alarm-notify`)
subscribes to the topic and emails you via **SES** (same verified sender as daily
briefings). No SNS "Confirm subscription" click is required.

| Alarm | What it catches |
|-------|-----------------|
| `soma-daily-pipeline-scheduler-target-errors` | EventBridge **Scheduler** `TargetErrorCount` for schedule `soma-daily-pipeline` (Lambda returned an error after invoke). |
| `soma-daily-pipeline-scheduler-invocations-dropped` | Scheduler **gave up** after retries (**InvocationDroppedCount**) — permissions, DLQ, or target misconfiguration. |
| `soma-daily-briefing-lambda-errors` | Lambda **Errors** (unhandled exception, timeout, etc.). |
| `soma-daily-briefing-lambda-throttles` | Lambda **Throttles**. |
| `soma-daily-briefing-user-pipeline-failures` | Log lines matching the per-user catch in `handler.py` (`Daily pipeline failed for user`). |
| `soma-hevy-ingest-scheduler-target-errors` | Hevy schedule: Scheduler `TargetErrorCount`. |
| `soma-hevy-ingest-scheduler-invocations-dropped` | Hevy schedule: **InvocationDroppedCount**. |
| `soma-hevy-ingest-lambda-errors` | Hevy ingest Lambda **Errors**. |
| `soma-caldav-ingest-scheduler-target-errors` | CalDAV schedule: Scheduler `TargetErrorCount`. |
| `soma-caldav-ingest-lambda-errors` | CalDAV ingest Lambda **Errors**. |
| `soma-strava-ingest-lambda-errors` | Strava ingest Lambda **Errors** (schedule currently disabled). |
| `soma-weekly-signal-lambda-errors` | Weekly signal Lambda **Errors**. |

**Subscribe your inbox** by passing CDK context at synth/deploy:

```bash
cdk deploy --all -c soma:pipelineAlarmEmail=you@example.com
```

`you@example.com` must be an SES-verified recipient if the account is still in the
SES sandbox (Soma's briefing recipient already is). The From address is
`SES_SENDER` from the `soma-briefing` secret.

In CI, set the GitHub Variable **`SOMA_ALARM_EMAIL`** on the `deploy` environment and
`deploy.yml` passes it as this context automatically.

> **Note:** AWS **Free Tier usage** emails (Billing console) are separate from these
> pipeline alarms. Free Tier alerts do not mean a Soma CloudWatch alarm fired.

**Inspect live alarm state** (OIDC → AWS): Actions → **Diagnose alarms** workflow, or:

```bash
aws cloudwatch describe-alarms --alarm-name-prefix soma- --state-value ALARM
aws sns list-subscriptions-by-topic --topic-arn "$(aws sns list-topics --query "Topics[?contains(TopicArn,'soma-daily-pipeline-alarms')].TopicArn" --output text)"
```

**Log group note:** The Lambda uses `log_retention` so CDK owns the CloudWatch log
group for metric filters. If deploy fails with "log group already exists", delete the
**empty** auto-created `/aws/lambda/soma-daily-briefing` log group in the console, then
redeploy once.

## Continuous deployment (GitHub Actions → AWS)

CI and deploy are wired via GitHub Actions using **OIDC → AWS IAM role** (no stored keys):
`ci.yml` (tests + synth) and `deploy.yml` (push to `main` + manual dispatch, gated on CI).
One-time AWS/GitHub setup and required environment variables are documented in
[`docs/plans/ci-aws.md`](../docs/plans/ci-aws.md).

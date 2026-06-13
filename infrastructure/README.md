# Soma — AWS CDK (Python)

Stable stack IDs (use these in docs, GitHub Actions, and CLI):

| Stack construct id | Purpose |
|--------------------|---------|
| **`SomaStagingStack`** | Staging Lambdas, buckets, rules, … |
| **`SomaProdStack`** | Production |

## Prereqs

- Python **3.14+** (same as repo `pyproject.toml`)
- From repo root: `pip install -e ".[cdk]"` **or** `pip install -r infrastructure/requirements.txt` inside a venv
- [AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html#getting_started_install) (`npm install -g aws-cdk` / `brew install aws-cdk`) **or** use repo root **`make cdk-synth`** (uses `npx aws-cdk@2`, no global install).

## Synth (no AWS call)

`python app.py` runs `app.synth()` but by default writes the assembly to a **temp** directory. For **`infrastructure/cdk.out/`** next to `cdk.json`, use the CDK CLI or Make:

```bash
# From repo root (recommended)
make cdk-synth

# Or from infrastructure/ with global cdk on PATH
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

Stacks are intentionally minimal (tags only) until pipeline Lambdas and data plane land in later work.

## Continuous deployment (GitHub Actions → AWS)

CI and deploys are wired via GitHub Actions using **OIDC → AWS IAM role** (no stored keys):
`ci.yml` (tests + synth), `deploy-staging.yml` (push to `main`), `deploy-prod.yml` (tag/dispatch + approval).
One-time AWS/GitHub setup and required environment variables are documented in
[`docs/plans/ci-aws.md`](../docs/plans/ci-aws.md).

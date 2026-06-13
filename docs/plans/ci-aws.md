# CI/CD: GitHub Actions → AWS (Phase 4)

How pushes turn into deploys, and the **one-time setup** you do in AWS and GitHub.

## Workflows

| File | Trigger | What it does |
|------|---------|--------------|
| [`ci.yml`](../../.github/workflows/ci.yml) | every PR + push to `main` (also reusable) | `pytest` on Python 3.14 + `cdk synth` (no AWS creds) |
| [`deploy-staging.yml`](../../.github/workflows/deploy-staging.yml) | push to `main`, manual | runs CI, then `cdk deploy SomaStagingStack` |
| [`deploy-prod.yml`](../../.github/workflows/deploy-prod.yml) | `v*` tag, manual dispatch | runs CI, waits for environment approval, then `cdk deploy SomaProdStack` |

Auth is **GitHub OIDC → an AWS IAM role** — no long-lived AWS keys are stored anywhere.

---

## 1. AWS — one time (single account, region `us-west-2` assumed)

> Replace `<ACCOUNT_ID>` with your 12-digit account id and `masomaster/soma` with your repo if it differs.

**a. Add GitHub as an OIDC identity provider** (skip if it already exists):

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

**b. Create the deploy role `soma-github-deploy`** with this trust policy (`trust.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:masomaster/soma:*" }
    }
  }]
}
```

```bash
aws iam create-role --role-name soma-github-deploy \
  --assume-role-policy-document file://trust.json
```

**c. Let that role drive CDK** by allowing it to assume the CDK bootstrap roles (`perms.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/cdk-hnb659fds-*"
  }]
}
```

```bash
aws iam put-role-policy --role-name soma-github-deploy \
  --policy-name soma-cdk-assume --policy-document file://perms.json
```

**d. Bootstrap the account/region once** (creates the `cdk-hnb659fds-*` roles + asset bucket):

```bash
cdk bootstrap aws://<ACCOUNT_ID>/us-west-2
```

Copy the role ARN — it looks like `arn:aws:iam::<ACCOUNT_ID>:role/soma-github-deploy`.

> Tip: To restrict staging vs prod to different sub-claims later, point each GitHub Environment at a **separate role** whose trust `sub` is scoped (e.g. `repo:masomaster/soma:environment:production`).

---

## 2. GitHub — one time

Repo → **Settings → Environments**, create two:

**`staging`**
- Variable `AWS_DEPLOY_ROLE_ARN` = the role ARN from step 1.
- Variable `AWS_REGION` = `us-west-2` (optional; this is the default).

**`production`**
- Same two variables.
- Under **Deployment protection rules**, enable **Required reviewers** (add yourself). This is the approval gate before prod deploys.

> These are GitHub **Variables**, not Secrets — a role ARN and region are not sensitive. Add real Secrets (e.g. `SUPABASE_SERVICE_ROLE_KEY`) only when a deploy step actually needs them.

(Optional) Protect `main` so merges require the **CI** check to pass.

---

## 3. `.env` (local only — NOT used by CI/CD)

CI authenticates via OIDC, so **no AWS secrets go in `.env`**. `.env` stays for local app/dev values only (see [`.env.example`](../../.env.example)):

```bash
# Local app dev (already documented):
ENV=local
# SUPABASE_URL=...
# SUPABASE_ANON_KEY=...
# ANTHROPIC_API_KEY=...
```

If you want to run `cdk deploy` **from your laptop**, use normal AWS CLI credentials (`aws configure` / SSO) — not `.env` — and optionally:

```bash
export CDK_DEFAULT_ACCOUNT=<ACCOUNT_ID>
export CDK_DEFAULT_REGION=us-west-2
```

---

## Quick test checklist (after setup)

1. Open a PR → **CI** runs (`pytest` + `cdk synth`).
2. Merge to `main` → **Deploy staging** runs CI then `cdk deploy SomaStagingStack`.
3. Push a tag `vX.Y.Z` (or use **Run workflow**) → **Deploy production** runs CI, pauses for your approval, then `cdk deploy SomaProdStack`.

Stacks are tags-only today, so a successful deploy creates two near-empty CloudFormation stacks named `SomaStagingStack` / `SomaProdStack` — proof the pipeline works before real resources land in later phases.

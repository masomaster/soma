# CI/CD: GitHub Actions → AWS (Phase 4)

How pushes turn into deploys, and the **one-time setup** you do in AWS and GitHub.

## Workflows

| File | Trigger | What it does |
|------|---------|--------------|
| [`ci.yml`](../../.github/workflows/ci.yml) | every PR + push to `main` (also reusable) | `pytest` on Python 3.14 + `cdk synth` (no AWS creds) |
| [`deploy.yml`](../../.github/workflows/deploy.yml) | push to `main`, manual dispatch | runs CI, then `cdk deploy --all` |

Soma has **one deployed environment**, so there is a single deploy workflow and a
single stack (see [infrastructure/README.md](../../infrastructure/README.md)).

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

**b. Create the deploy role `soma-github-deploy`** with this trust policy (`trust.json`).

> **Security:** scope the `sub` claim to the **deploy environment**, not `repo:masomaster/soma:*`. The deploy job declares `environment: deploy`, so its OIDC subject is the `…:environment:deploy` form. Restricting to this subject means an arbitrary branch or pull request (which has no environment) cannot assume this role.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:masomaster/soma:environment:deploy"
      }
    }
  }]
}
```

> Already created the role with the broader `repo:masomaster/soma:*`? Update the trust policy in IAM → Roles → `soma-github-deploy` → Trust relationships to the scoped version above.

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

---

## 2. GitHub — one time

Repo → **Settings → Environments**, create one:

**`deploy`**
- Variable `AWS_DEPLOY_ROLE_ARN` = the role ARN from step 1.
- Variable `AWS_REGION` = `us-west-2` (optional; this is the default).
- Variable `SOMA_ALARM_EMAIL` (optional) = inbox for pipeline CloudWatch alarms. When set, each deploy (re)creates the SNS email subscription; AWS emails a one-time confirmation you must click. Leave unset to manage alarm subscriptions manually.
- (Optional) Under **Deployment protection rules**, enable **Required reviewers** if you want a manual approval gate before every deploy.

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
2. Merge to `main` (or use **Run workflow**) → **Deploy** runs CI then `cdk deploy --all`.

The single CloudFormation stack keeps the id `SomaStagingStack` so deploys update the
existing environment in place (see [infrastructure/README.md](../../infrastructure/README.md)).

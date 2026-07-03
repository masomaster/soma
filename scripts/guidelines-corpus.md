# Guidelines corpus (Phase 10 operator flow)

One-time setup for per-user briefing context: `my-goals.md`, `injury-history.md`, and
`expert-principles.md`. Runtime loading is implemented in `pipeline/guidelines.py`.

## Storage paths

| Environment | Location |
|-------------|----------|
| **Local** | `SOMA_GUIDELINES_LOCAL_DIR/guidelines/{user_id}/` |
| **AWS** | S3 bucket from CDK output `GuidelinesBucketName` → `guidelines/{user_id}/` |

The daily briefing Lambda reads these files; the Streamlit dashboard can **append** to
`my-goals.md` via coaching chat (`append_goal_note`).

## Expert principles corpus (manual, ToS-safe)

1. Pick ~12 trusted YouTube channels (e.g. Mike Israetel, Jeremy Ethier, Jeff Nippard).
2. For each video, obtain captions **manually** (YouTube Studio export, official captions,
   or a transcript you own). **Do not** automate scraping in this repo.
3. Optionally run a local script with `ANTHROPIC_API_KEY` to condense transcripts into
   bullets — always **human-review** before upload.
4. Start from the skeleton at
   `tests/fixtures/guidelines/guidelines/demo-user/expert-principles.md`.
5. Upload to S3 (example — replace bucket and user id):

```bash
aws s3 cp expert-principles.md \
  s3://YOUR_GUIDELINES_BUCKET/guidelines/YOUR_USER_ID/expert-principles.md \
  --content-type text/markdown
```

Repeat for `my-goals.md` and `injury-history.md` as needed.

## Dashboard + email link

After `cdk deploy`, the stack emits **`DashboardUrl`** (ALB HTTP endpoint). The briefing
Lambda receives the same value as `BRIEFING_EMAIL_DASHBOARD_URL` so morning emails include
an “Open your dashboard” footer. Add ACM + Route53 on your domain for HTTPS when ready.

Fill **`soma-dashboard`** in Secrets Manager with:

- `SUPABASE_URL` — project URL from Supabase Dashboard
- `SUPABASE_ANON_KEY` — publishable anon key (RLS protects rows)
- `ANTHROPIC_API_KEY` — for coaching chat and history queries

`SOMA_DATABASE_URL` is injected from the existing **`soma-db`** secret (session pooler URI).

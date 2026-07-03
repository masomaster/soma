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

Deploy the dashboard on **Streamlit Community Cloud** (free), then wire the URL into the
briefing Lambda via `cdk deploy -c soma:dashboardUrl=https://YOUR-APP.streamlit.app` or
GitHub variable **`SOMA_DASHBOARD_URL`**. See [`docs/plans/dashboard-hosting.md`](../docs/plans/dashboard-hosting.md).

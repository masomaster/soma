# Guidelines corpus (Phase 10 operator flow)

One-time setup for per-user briefing + coaching-chat context: `my-goals.md`,
`injury-history.md`, and `expert-principles.md`. Runtime loading is implemented
in `pipeline/guidelines.py`.

## Storage paths

| Environment | Location |
|-------------|----------|
| **Local** | `SOMA_GUIDELINES_LOCAL_DIR/guidelines/{user_id}/` (default `tmp/soma_guidelines/`) |
| **AWS** | S3 bucket from CDK output `GuidelinesBucketName` → `guidelines/{user_id}/` |

The daily briefing Lambda and Streamlit coaching chat **read** these files; chat
can **append** to `my-goals.md` via `append_goal_note`.

## Expert principles from science-based lifting transcripts

`expert-principles.md` is the workout-advice corpus. Distill owned/official
captions (Mike Israetel / RP, Jeremy Ethier, Jeff Nippard, etc.) into short
citeable bullets — the LLM must **cite this file**, not invent principles.

### Operator steps (ToS-safe)

1. Pick trusted videos (~12 is plenty). Obtain captions **manually** (YouTube
   Studio export, official captions download, or a transcript you own).
   **Do not** automate scraping in this repo.
2. Drop each transcript as `.md` / `.txt` under `tmp/guidelines-transcripts/`
   (gitignored). Optional YAML frontmatter:

   ```markdown
   ---
   source: Jeff Nippard
   title: Progressive Overload Explained
   url: https://www.youtube.com/watch?v=…
   date: 2024-06-01
   ---

   …transcript text…
   ```

3. Condense (pick one):
   - **Cursor / chat:** paste transcripts into the cloud agent or local Cursor
     session and ask it to update `expert-principles.md` using the skeleton
     sections (volume, overload, recovery, cardio, injury prevention).
   - **CLI prompt only** (no API):

     ```bash
     .venv/bin/python scripts/condense_expert_principles.py --print-prompt
     ```

   - **CLI + Anthropic:**

     ```bash
     ANTHROPIC_API_KEY=… .venv/bin/python scripts/condense_expert_principles.py \
       --llm \
       --output tmp/soma_guidelines/guidelines/$SOMA_USER_ID/expert-principles.md
     ```

4. **Human-review** the draft. Keep it under ~4000 characters (prompt injection
   truncates at `pipeline.guidelines.DEFAULT_MAX_CHARS`).
5. Upload:

   ```bash
   make guidelines-sync
   ```

   Or a single-file copy:

   ```bash
   aws s3 cp expert-principles.md \
     s3://YOUR_GUIDELINES_BUCKET/guidelines/YOUR_USER_ID/expert-principles.md \
     --content-type text/markdown
   ```

Starter skeleton (also the test fixture):
`tests/fixtures/guidelines/guidelines/demo-user/expert-principles.md`.
Regenerate a local copy with:

```bash
.venv/bin/python scripts/condense_expert_principles.py --skeleton \
  --output tmp/soma_guidelines/guidelines/$SOMA_USER_ID/expert-principles.md
```

Repeat for `my-goals.md` and `injury-history.md` as needed (those are personal,
not transcript-derived).

## Dashboard + email link

Deploy the dashboard on **Streamlit Community Cloud** (free), then wire the URL
into the briefing Lambda via
`cdk deploy -c soma:dashboardUrl=https://YOUR-APP.streamlit.app` or GitHub
variable **`SOMA_DASHBOARD_URL`**. See
[`docs/plans/dashboard-hosting.md`](../docs/plans/dashboard-hosting.md).

# Dashboard hosting (single user, public URL)

> **Live:** https://somaapp.streamlit.app — deployed on Streamlit Community Cloud. Briefing emails link here via the GitHub variable `SOMA_DASHBOARD_URL`.

Soma’s dashboard is **`dashboard/app.py`** (Streamlit). For a **free, HTTPS, any-device** URL with **Supabase Auth** in front of your data, deploy to **[Streamlit Community Cloud](https://streamlit.io/cloud)** — not AWS containers.

**Cost:** $0 on the Community Cloud hobby tier for a personal app.

**Why not AWS Fargate/ALB:** ~$55+/month for an always-on container + load balancer. Overkill for one user.

**Why not Vercel:** Streamlit is a long-running Python server; Vercel would require rewriting the UI in Next.js. Community Cloud runs the existing app unchanged.

---

## 1. Deploy on Streamlit Community Cloud

1. Push this repo to GitHub (already done if you are reading this there).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. **Repository / branch / main file:**
   - Repo: `masomaster/soma` (your fork)
   - Branch: `main`
   - Main file path: **`dashboard/app.py`**
4. **Advanced settings → Python version:** pick the **highest available** (3.13+ if 3.14 is not listed yet).
5. **Secrets:** paste the TOML from [`.streamlit/secrets.toml.example`](../../.streamlit/secrets.toml.example) (fill real values in the Cloud UI — never commit secrets).
6. Deploy. Copy the app URL (e.g. `https://your-app-name.streamlit.app`).

`requirements.txt` at the repo root lists dashboard dependencies; Cloud runs `pip install -r requirements.txt`.

---

## 2. Wire the URL into briefing emails

After deploy, pass your public URL to CDK so the daily briefing Lambda includes an “Open your dashboard” footer:

```bash
cd infrastructure
cdk deploy --all -c soma:dashboardUrl=https://YOUR-APP.streamlit.app
```

Or set GitHub environment variable **`SOMA_DASHBOARD_URL`** (see `.github/workflows/deploy.yml`) so deploys keep it in sync.

You can also set `BRIEFING_EMAIL_DASHBOARD_URL` manually on the `soma-daily-briefing` Lambda in the AWS console.

---

## 3. Supabase Auth (safety)

- Create your user in **Supabase Dashboard → Authentication** (self-service sign-up is **disabled** on the cloud dashboard via `SOMA_CLOUD_DASHBOARD=1`).
- In **Supabase → Authentication → URL configuration**, add your Streamlit app URL to **Redirect URLs** if you add OAuth later.
- RLS still isolates rows: the app signs in with the **anon key + user JWT** and scopes Postgres with `apply_rls_scope`.

---

## 4. Optional: guidelines S3 from the cloud app

Coaching chat can append to `my-goals.md` in the guidelines bucket. On Streamlit Cloud there is no AWS instance role — add **read-only or read-write IAM user keys** to Streamlit secrets (minimal policy on `guidelines/{user_id}/*` only):

```toml
SOMA_GUIDELINES_BUCKET = "your-guidelines-bucket"
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-west-2"
```

If you omit AWS keys, guidelines still load when objects are public (they are not) or you skip S3 — briefing Lambda still reads guidelines from S3 on schedule.

---

## 5. Local development (unchanged)

```bash
make dashboard          # fixture mode
make dashboard-live     # your Supabase data via .env
```

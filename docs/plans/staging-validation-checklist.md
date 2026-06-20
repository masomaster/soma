# Staging validation checklist (Phase 7–8)

Operator steps after **`SomaStagingStack`** is deployed and **`soma-*`** secrets are filled. Repo build for Phases 7–8 is complete; this doc tracks **live staging soak** on your Supabase + AWS account.

Replace `<user_id>` with your **`soma-tenant`** UUID (`SOMA_USER_ID` / `auth.users.id`).

---

## Quick status (single-user staging)

| Step | Status | Doc section |
|------|--------|-------------|
| Split secrets (`soma-db`, `soma-hevy`, …) | ✅ Done | [infrastructure/lambda/briefing/README.md](../../infrastructure/lambda/briefing/README.md) |
| Migration **`0004_signal_layers.sql`** applied | ✅ Done | [db-access-patterns.md](./db-access-patterns.md) |
| Migration **`0005_goals_and_product.sql`** applied | ✅ Done | [db-access-patterns.md](./db-access-patterns.md) |
| **`user_settings`** row + SES identity | ✅ Done | [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md) |
| Apple Health (HAE) wired | ✅ Done | [apple-health-export.md](./apple-health-export.md) |
| Health Sync → Apple Health | ✅ Done | [integrations-checklist.md](./integrations-checklist.md) |
| CalDAV (`CALDAV_CALENDAR_NAME=Mason`) | ✅ Done | [caldav_ingest README](../../infrastructure/lambda/caldav_ingest/README.md) |
| Hevy historical backfill | ✅ Done | § Hevy backfill below |
| Daily briefing SES smoke | ✅ Done | [briefing-staging-inbox-checklist.md](./briefing-staging-inbox-checklist.md) |
| Phase 8 baselines / patterns (post-briefing) | ✅ Done | § Phase 8 below |
| Weekly signal job | ✅ Done | `aws lambda invoke … soma-staging-weekly-signal` |

**CalDAV note:** Only the **`Mason`** calendar is ingested (`interventions.category = calendar_busy`). Events on shared calendars (e.g. **`Caroline`**) are **intentionally excluded** — they do not necessarily block your training window.

---

## Hevy backfill — confirm or run

### Why it matters

- **Scheduled ingest** (`soma-staging-hevy-ingest`, 09:00 UTC) only pulls **new** workouts after deploy.
- **Backfill** paginates **all** Hevy history into `strength_events` so ACWR, tonnage, and strength-related briefing flags have data from day one.
- **Safe to re-run:** inserts use `ON CONFLICT DO NOTHING` on `(user_id, source_id)`.

### 1. Confirm whether backfill is already done

In **Supabase SQL Editor** (staging project):

```sql
-- Total Hevy rows for your user
SELECT COUNT(*) AS hevy_rows
FROM strength_events
WHERE user_id = '<user_id>' AND source = 'hevy';

-- Date span (oldest → newest workout day)
SELECT MIN(event_date) AS oldest, MAX(event_date) AS newest, COUNT(*) AS rows
FROM strength_events
WHERE user_id = '<user_id>' AND source = 'hevy';

-- Recent sample
SELECT event_date, exercise_name, source_id
FROM strength_events
WHERE user_id = '<user_id>' AND source = 'hevy'
ORDER BY event_date DESC, created_at DESC
LIMIT 10;
```

**Interpret:**

| Result | Meaning |
|--------|---------|
| `hevy_rows = 0` | Backfill **not done** (unless you truly have no Hevy history). Run backfill below. |
| `hevy_rows > 0` but `oldest` is only a few days ago | Partial history — scheduled ingest may have run, but **full backfill likely not**. Compare `oldest` to when you started using Hevy. |
| `oldest` matches your first Hevy workouts | Backfill **done** (or history is genuinely short). No action unless you want to refresh. |

Optional: check local raw files from a prior backfill:

```bash
ls tmp/soma_raw/raw/<user_id>/hevy/ 2>/dev/null | head
```

### 2. Run backfill (from repo root)

Ensure `.env` has (same values as `soma-hevy`, `soma-tenant`, `soma-db`):

```bash
HEVY_API_KEY=...
SOMA_USER_ID=<user_id>
SOMA_DATABASE_URL=postgresql://...pooler...   # session pooler URI
```

Then:

```bash
pip install -e ".[dev]"   # if needed
python scripts/smoke_hevy.py backfill
```

Expected output: `backfill: OK` with a **normalized row count** (often hundreds+ if you have history). Raw JSON lands under `tmp/soma_raw/raw/<user_id>/hevy/` unless disabled.

**Env toggles:**

| Variable | Default | Effect |
|----------|---------|--------|
| `SOMA_HEVY_BACKFILL_RAW` | `1` | Write raw JSON pages to disk |
| `SOMA_HEVY_BACKFILL_SKIP_DB` | unset | Set to `1` for raw-only dry run |

### 3. Alternative: AWS Lambda (incremental only)

Manual invoke pulls **recent pages**, not full history:

```bash
aws lambda invoke --function-name soma-staging-hevy-ingest \
  --payload '{}' /tmp/hevy-out.json && cat /tmp/hevy-out.json
```

Use this to verify secrets/IAM; use **`smoke_hevy.py backfill`** for history.

### 4. Re-run SQL from step 1

Confirm row count and `oldest`/`newest` look right before relying on strength features in the daily briefing.

---

## Other Phase 7 ingests (reference)

| Source | Confirm in SQL |
|--------|----------------|
| Apple Health | `SELECT COUNT(*) FROM biometrics WHERE user_id = '<user_id>';` |
| CalDAV (Mason only) | `SELECT COUNT(*) FROM interventions WHERE user_id = '<user_id>' AND notes = 'caldav_icloud';` |
| Hevy | § above |

Local smokes: [local-dev-and-tooling.md](./local-dev-and-tooling.md) (`smoke_hevy.py`, `smoke_apple_health.py`, `smoke_caldav.py`).

---

## Phase 8 (reference)

After **`0004`** is applied and the daily briefing has run at least once:

```sql
SELECT COUNT(*) FROM metric_baselines WHERE user_id = '<user_id>';
SELECT COUNT(*) FROM metric_patterns WHERE user_id = '<user_id>';
SELECT COUNT(*) FROM anomaly_events WHERE user_id = '<user_id>' AND anomaly_type = 'statistical';
```

Empty **`anomaly_events`** is normal for the first ~2 weeks (z-scores need baseline history). Optional Sonnet patterns: set **`ENABLE_WEEKLY_PATTERN_LLM=1`** on `soma-staging-weekly-signal` and invoke the weekly Lambda.

---

## Sign-off

| Date | Hevy backfill confirmed | Briefing SES OK | Notes |
|------|-------------------------|-----------------|-------|
| 2026-06-19 | ✅ | ✅ | Single-user staging soak complete (Phases 7–8). Migrations through `0005`, secrets, ingests, daily briefing, Phase 8 tables verified. |

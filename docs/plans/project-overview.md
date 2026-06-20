# Soma — Personal Health OS

*Synthesized from conversation with Claude, June 2026*

---

## Vision

Soma is a personal health operating system that aggregates data from every fitness and health platform you use, normalizes it into a queryable database, and runs a daily LLM-powered coaching pipeline — attending to sleep, recovery, training load, and long-term goals like cholesterol reduction, aerobic fitness, and strength progression. The system has read-only access to health/fitness data and iCloud calendar, and delivers a 6 AM daily briefing with actionable, personalized coaching. Eventually: a natural language frontend for querying fitness history over months and years, and a proactive anomaly detection layer that surfaces patterns you didn't think to look for.

**Designed from the start for:**
- Multiple users (auth, user-scoped data)
- Staging and production environments
- Local development without cloud dependencies
- Future-proofing against service changes

---

## Data Sources

| Source | Data Type | Integration Method | Difficulty |
|---|---|---|---|
| **Hevy** | Lifting — sets, reps, weight, RPE | Official API (Pro required) | Easy |
| **Apple Health** | Steps, HRV, sleep, VO2 max, resting HR | Health Auto Export iOS app → webhook | Easy |
| **Strava** | Running/cycling — GPS, pace, HR, elevation | Official API | Easy |
| **Fitbit/Google Health** | Sleep stages, resting HR, HRV, weight | Google Health API (OAuth2) | Medium |
| **Nike Run Club** | Historical run data | Bearer token scrape (`nrc-exporter`) — fragile | Hard |
| **Renpho** | Body comp — weight, fat %, muscle mass | `renpho-api` PyPI package | Medium |
| **iCloud Calendar** | Schedule, free/busy blocks | CalDAV with app-specific password | Medium |

### Notes on Source Priority

- **Apple Health as biometric hub**: Fitbit, Strava, and NRC all sync into Apple Health. The Health Auto Export app can capture all of this via one integration.
- **NRC**: Not worth maintaining long-term. Use `nrc-exporter` once for historical bulk import, then let Apple Health serve as its proxy.
- **iCloud Calendar**: No modern REST API — uses CalDAV (RFC standard). App-specific password at `appleid.apple.com`. Read-only, polling only. Python library: `caldav` (PyPI).
- **⚠️ Fitbit API sunset**: The legacy Fitbit Web API sunsets September 2026. Build against the new Google Health API from day one.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Data Sources                         │
│  Hevy · Apple Health · Strava · Fitbit · Renpho · NRC  │
└────────────────────┬────────────────────────────────────┘
                     │  (ETL cron — 5:50 AM)
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Raw Ingestion Layer                        │
│   S3: /raw/{user_id}/{source}/{date}/response.json      │
│   Exact API responses, unmodified. Permanent.           │
└────────────────────┬────────────────────────────────────┘
                     │  (normalize & upsert)
                     ▼
┌─────────────────────────────────────────────────────────┐
│         Normalized Event Store (per-user)               │
│         (Supabase PostgreSQL + Row-Level Security)      │
│  strength_events · cardio_events · biometrics           │
│  daily_health_metrics · interventions                   │
└────────────────────┬────────────────────────────────────┘
                     │  (Analysis cron — 5:55 AM)
                     ▼
┌─────────────────────────────────────────────────────────┐
│           Daily Feature Computation                     │
│   Derives training load, rolling windows, trends        │
│   Persists to daily_features table                      │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────────┐  ┌────────────────────────────────┐
│   Rules Engine       │  │   Anomaly Detection Engine     │
│   (deterministic,    │  │   Statistical layer (daily)    │
│   SSM thresholds)    │  │   LLM pattern scan (weekly)    │
└──────────┬───────────┘  └──────────────┬─────────────────┘
           │                             │
           └──────────────┬──────────────┘
                          │  (LLM call — 6:00 AM)
                          ▼
┌─────────────────────────────────────────────────────────┐
│         Claude API (claude-haiku-4-5)                   │
│         Narrative synthesis — not reasoning             │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌─────────────────────────────────┐
│  Daily Briefing  │  │  Saved to DB (daily_briefings)  │
│  via SES email   │  │  features + flags + note        │
└──────────────────┘  └─────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│          Natural Language Query Frontend                │
│     (Future) Text → SQL → chart / narrative answer      │
│     Queries all tables including briefing history       │
└─────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Raw before normalized.** Every API response is written to S3 unchanged before normalization. If a bug corrupts normalized data, you replay from raw. This is the most important recovery mechanism in the system.
2. **The LLM explains, it doesn't reason.** The Rules Engine and Anomaly Engine do the logic. The LLM turns pre-computed conclusions into prose.
3. **All data is user-scoped.** Every table has a `user_id` foreign key. Row-Level Security enforces isolation at the database layer.
4. **Service-agnostic schema.** Tables model events and metrics, not app-specific data shapes. Swapping a service means rewriting one ETL adapter, not migrating the database.
5. **Every pipeline result is persisted.** Daily features, flags, and coaching notes are saved to the database, enabling trend analysis on the pipeline output itself.
6. **No archival.** Health data is kept hot in Supabase indefinitely. At this volume (one person, structured rows), the free tier lasts decades. The complexity of Parquet archival is not worth it.

---

## Multi-User Architecture

The system is built as a single-user app today but designed from day one to support multiple users without schema changes.

### Authentication

**Supabase Auth** handles user management. It provides JWT-based authentication, email/password and OAuth (Google, Apple) sign-in, and integrates directly with Row-Level Security policies on your tables.

For the initial single-user build: create one user account in Supabase Auth and use that `user_id` everywhere. When you add a second user later, RLS automatically enforces data isolation — no application-level changes needed.

### Row-Level Security (RLS)

Every data table has a `user_id` column and RLS policies that restrict reads and writes to the authenticated user's own rows.

```sql
-- Applied to ALL tables: strength_events, cardio_events,
-- biometrics, daily_briefings, anomaly_events

-- Enable RLS
ALTER TABLE strength_events ENABLE ROW LEVEL SECURITY;

-- Users can only see and write their own rows
CREATE POLICY "user_isolation" ON strength_events
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
```

This means the API endpoint, the query frontend, and the natural language query system never need to filter by `user_id` in application code — the database enforces it automatically for any authenticated request.

### User-Specific Configuration

Each user has their own:
- Guidelines files in S3, keyed by `user_id`: `s3://bucket/users/{user_id}/guidelines/my-goals.md`, `injury-history.md`, `expert-principles.md`
- SSM parameters scoped per user: `/soma/{user_id}/rules/cardio_weekly_min`
- ETL credentials in Secrets Manager: `soma/{user_id}/hevy-api-key`
- Delivery preferences (email address, notification time) in a `user_settings` table

The Lambda functions receive a `user_id` in their event payload and load all config/secrets scoped to that user. For the current single-user build, this is just your ID — but the routing logic is already correct for multiple users.

```sql
CREATE TABLE user_settings (
    user_id     UUID PRIMARY KEY REFERENCES auth.users(id),
    email       TEXT NOT NULL,
    timezone    TEXT DEFAULT 'America/Los_Angeles',
    briefing_time TIME DEFAULT '06:00:00',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Environments: Local, Staging, Production

Three environments. Each is fully isolated. No environment touches another's data.

### Environment Summary

| | Local | Staging | Production |
|---|---|---|---|
| **Purpose** | Development, schema iteration | Integration testing, pre-deploy validation | Live daily briefings |
| **Database** | Local PostgreSQL (Docker) | Supabase staging project | Supabase prod project |
| **AWS** | LocalStack (mocked) | Real AWS, `soma-staging` namespace | Real AWS, `soma-prod` namespace |
| **LLM** | Claude API (real, low spend) | Claude API (real) | Claude API (real) |
| **Data** | Synthetic seed data + sampled real data | Sampled real data | Real data |
| **Briefing delivery** | stdout / local log | Your email, marked `[STAGING]` | Your email |

### Local Development Setup

The goal: run the entire pipeline on your laptop with no cloud dependencies, except the LLM API (which is cheap enough to use directly).

**Prerequisites:**
- Docker Desktop
- Python 3.12+
- `pip install supabase anthropic caldav boto3 localstack`

**Local PostgreSQL via Docker:**

```bash
# Start a local Postgres that mirrors the Supabase schema
docker run -d \
  --name soma-local \
  -e POSTGRES_PASSWORD=localpass \
  -e POSTGRES_DB=soma \
  -p 5432:5432 \
  postgres:16

# Apply your schema
psql -h localhost -U postgres -d soma -f schema.sql
```

**LocalStack for AWS services (S3, SSM, SES mocking):**

```bash
# Start LocalStack (mocks S3, SSM, SES, Secrets Manager)
docker run -d \
  --name localstack \
  -e SERVICES=s3,ssm,secretsmanager,ses \
  -p 4566:4566 \
  localstack/localstack

# Seed SSM parameters locally
aws --endpoint-url=http://localhost:4566 ssm put-parameter \
  --name "/soma/rules/cardio_weekly_min" \
  --value "3" --type String
```

**Environment config via `.env` files (never committed):**

```bash
# .env.local
ENV=local
SUPABASE_URL=postgresql://postgres:localpass@localhost:5432/soma
AWS_ENDPOINT_URL=http://localhost:4566  # LocalStack
ANTHROPIC_API_KEY=sk-ant-...            # real key, usage is tiny locally
```

```bash
# .env.staging  (in Secrets Manager for Lambda, not on disk)
ENV=staging
SUPABASE_URL=https://abc123.supabase.co  # staging Supabase project
# AWS uses real services, no endpoint override
```

**Seed data for local development:**

```python
# scripts/seed_local.py
# Inserts synthetic but realistic data for development/testing
# Covers 90 days: strength sessions 3-4x/week, cardio 2-3x/week,
# daily biometrics with realistic HRV/sleep variance

import random
from datetime import date, timedelta

def seed(supabase, user_id: str):
    for i in range(90):
        d = date.today() - timedelta(days=i)
        supabase.table("biometrics").insert([
            {"user_id": user_id, "source": "seed", "event_date": str(d),
             "metric": "hrv_rmssd", "value": random.gauss(52, 8), "unit": "ms"},
            {"user_id": user_id, "source": "seed", "event_date": str(d),
             "metric": "sleep_hours", "value": random.gauss(7.0, 0.8), "unit": "hours"},
            {"user_id": user_id, "source": "seed", "event_date": str(d),
             "metric": "resting_hr", "value": random.gauss(58, 5), "unit": "bpm"},
        ]).execute()
```

**Running the pipeline locally:**

```bash
# Run ETL against Hevy API, write to local Postgres
ENV=local python -m pipeline.etl --source hevy --user-id local-dev-user

# Run Rules Engine
ENV=local python -m pipeline.rules --user-id local-dev-user

# Run briefing (prints to stdout instead of sending email)
ENV=local python -m pipeline.briefing --user-id local-dev-user

# Run anomaly detection
ENV=local python -m pipeline.anomaly --user-id local-dev-user
```

All Lambda handlers accept `ENV` as an environment variable. When `ENV=local`, they write to stdout and skip SES delivery. When `ENV=staging` or `ENV=prod`, they send real email.

### Staging Environment

A second Supabase project (`soma-staging`) and a parallel AWS namespace (`soma-staging-*`). Mirrors production exactly.

**Staging-specific behavior:**
- Email subject prefix: `[STAGING]`
- SSM parameters at `/soma-staging/rules/...`
- Secrets at `soma-staging/{user-id}/...`
- EventBridge rules with `-staging` suffix

Deploy to staging and let it run for 2-3 days before deploying the same code to production. Staging catches integration issues (API field changes, Supabase schema drift, SSM parameter mismatches) before they affect your real daily briefing.

**Promotion flow:**

```
local dev → passes local tests
         → push to GitHub → CI runs unit tests
         → deploy to staging → let run 2-3 days
         → deploy to production
```

### Infrastructure as Code

Use **AWS CDK v2 (Python)** for all AWS resources. Same app can deploy **staging** and **production** via separate **stacks** or **stages** (different names, buckets, Lambdas, SSM path prefixes). **Terraform and SAM are not part of this project.**

```
infrastructure/
  app.py                 # CDK App — registers SomaStagingStack + SomaProdStack
  cdk.json
  requirements.txt       # pins (mirror optional [cdk] in repo pyproject.toml)
  soma_cdk/
    staging_stack.py     # class SomaStagingStack
    prod_stack.py        # class SomaProdStack
```

---

## Raw Ingestion Layer

Before any normalization happens, every API response is written to S3 exactly as received. This is the most important architectural decision in the ETL pipeline.

```
S3 bucket: your-soma-bucket
Path:      /raw/{user_id}/{source}/{YYYY-MM-DD}/{timestamp}.json

Examples:
  /raw/abc123/hevy/2026-06-07/135900.json
  /raw/abc123/strava/2026-06-07/135901.json
  /raw/abc123/apple_health/2026-06-07/135902.json
  /raw/abc123/renpho/2026-06-07/135903.json
```

### Why This Matters

- **Bug recovery**: When (not if) a normalization bug corrupts 3 months of data, you replay from raw with a fixed adapter. Without raw storage, that data is gone.
- **Schema evolution**: When you realize you should have been capturing a field you ignored, you can backfill from raw without calling the API again.
- **Source API changes**: When Hevy changes a field name, you have the original response to verify what changed.
- **Debugging**: "Did the API actually return null here, or did my code introduce that?" is instantly answerable.

### Implementation

```python
# In every ETL adapter, raw write happens FIRST
def fetch_and_store_raw(source: str, user_id: str, response_data: dict) -> str:
    """Write raw API response to S3 before any normalization."""
    s3 = boto3.client("s3")
    timestamp = datetime.utcnow().strftime("%H%M%S")
    today = date.today().isoformat()

    key = f"raw/{user_id}/{source}/{today}/{timestamp}.json"
    s3.put_object(
        Bucket=os.environ["S3_BUCKET"],
        Key=key,
        Body=json.dumps(response_data, default=str),
        ContentType="application/json"
    )
    return key  # store in DB row for traceability

# Adapter pattern: raw write → normalize → upsert
def run_hevy_etl(user_id: str, secrets: dict):
    raw = fetch_from_hevy_api(secrets["HEVY_API_KEY"])         # 1. fetch
    raw_key = fetch_and_store_raw("hevy", user_id, raw)        # 2. write raw
    rows = normalize_hevy(raw, user_id)                        # 3. normalize
    upsert_to_supabase("strength_events", rows)                # 4. upsert
```

### Raw Data Retention

Raw JSON files are kept forever. At ~10KB per source per day across 6 sources, this is roughly 20MB/year — negligible on S3 at $0.023/GB/month. For 5 years: ~$0.002/month.

Apply a standard S3 Intelligent-Tiering policy on `/raw/` — AWS automatically moves cold files to cheaper storage tiers without any lifecycle rules to configure.

### Replay Pattern

```python
# scripts/replay_etl.py
# Re-normalizes all raw files for a source between two dates
# Use when a bug is discovered in a normalization adapter

def replay(user_id: str, source: str, start_date: str, end_date: str):
    s3 = boto3.client("s3")
    prefix = f"raw/{user_id}/{source}/"

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            # Filter by date range from key path
            date_part = obj["Key"].split("/")[3]  # YYYY-MM-DD
            if start_date <= date_part <= end_date:
                raw = json.loads(s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read())
                rows = normalize_hevy(raw, user_id)   # re-run with fixed adapter
                upsert_to_supabase("strength_events", rows)

    print(f"Replayed {source} for {user_id} from {start_date} to {end_date}")
```

---

All data is stored as structured rows in PostgreSQL (Supabase), not markdown summaries or flat files.

### Schema Design Philosophy

The schema is service-agnostic. No table is named after a specific app. No column assumes a particular API's field names. If Hevy disappears, the `strength_events` table doesn't change — only the ETL adapter does. The schema models *what happened*, not *what app recorded it*.

Every table includes `user_id` for multi-user isolation and `source`/`source_id` for deduplication and provenance.

```sql
-- -------------------------------------------------------
-- USERS (extends Supabase Auth)
-- -------------------------------------------------------
CREATE TABLE user_settings (
    user_id       UUID PRIMARY KEY REFERENCES auth.users(id),
    email         TEXT NOT NULL,
    timezone      TEXT DEFAULT 'America/Los_Angeles',
    briefing_time TIME DEFAULT '06:00:00',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- STRENGTH TRAINING
-- One row per set. Works for Hevy, Strong, Fitbod, etc.
-- -------------------------------------------------------
CREATE TABLE strength_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id),
    source        TEXT NOT NULL,        -- 'hevy', 'strong', 'manual'
    source_id     TEXT,                 -- for dedup
    event_date    DATE NOT NULL,
    exercise_name TEXT NOT NULL,
    muscle_group  TEXT,                 -- 'push', 'pull', 'legs', 'core'
    movement_type TEXT,                 -- 'compound', 'isolation'
    set_number    INT,
    reps          INT,
    weight_lbs    FLOAT,
    rpe           FLOAT,
    set_type      TEXT,                 -- 'working', 'warmup', 'dropset'
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- CARDIO / ENDURANCE ACTIVITIES
-- One row per session. Works for Strava, NRC, Garmin, etc.
-- -------------------------------------------------------
CREATE TABLE cardio_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    source          TEXT NOT NULL,
    source_id       TEXT,
    event_date      DATE NOT NULL,
    activity_type   TEXT NOT NULL,      -- 'run', 'ride', 'swim', 'hike', 'walk'
    duration_min    FLOAT,
    distance_miles  FLOAT,
    elevation_ft    FLOAT,
    avg_hr          INT,
    max_hr          INT,
    avg_pace_sec_mi INT,
    calories        INT,
    effort_zone     TEXT,               -- 'zone1'–'zone5'
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- BIOMETRICS (EAV — flexible ingestion layer)
-- One row per metric per day per source.
-- Good for ingestion; use daily_health_metrics for analysis.
-- -------------------------------------------------------
CREATE TABLE biometrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users(id),
    source      TEXT NOT NULL,
    event_date  DATE NOT NULL,
    metric      TEXT NOT NULL,
    value       FLOAT NOT NULL,
    unit        TEXT,
    raw_s3_key  TEXT,              -- traceability back to raw file
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source, event_date, metric)
);

-- Canonical metric names (all ETL adapters must use these exact strings):
-- 'hrv_rmssd'        ms
-- 'resting_hr'       bpm
-- 'sleep_hours'      hours
-- 'sleep_deep_hrs'   hours
-- 'sleep_rem_hrs'    hours
-- 'sleep_score'      0-100
-- 'steps'            count
-- 'active_cal'       kcal
-- 'vo2_max'          ml/kg/min
-- 'body_weight_lbs'  lbs
-- 'body_fat_pct'     pct
-- 'muscle_mass_lbs'  lbs
-- 'spo2_pct'         pct
-- 'respiratory_rate' breaths/min

-- -------------------------------------------------------
-- DAILY HEALTH METRICS (wide table — analysis layer)
-- Derived from biometrics. One row per user per day.
-- Columns for every metric: easy pivots, easy charting,
-- easy text-to-SQL, easy anomaly detection.
-- -------------------------------------------------------
CREATE TABLE daily_health_metrics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id),
    metric_date         DATE NOT NULL,
    -- Recovery
    hrv_rmssd           FLOAT,
    resting_hr          INT,
    spo2_pct            FLOAT,
    respiratory_rate    FLOAT,
    -- Sleep
    sleep_hours         FLOAT,
    sleep_deep_hrs      FLOAT,
    sleep_rem_hrs       FLOAT,
    sleep_score         FLOAT,
    -- Activity
    steps               INT,
    active_cal          INT,
    vo2_max             FLOAT,
    -- Body composition
    body_weight_lbs     FLOAT,
    body_fat_pct        FLOAT,
    muscle_mass_lbs     FLOAT,
    -- Derived / computed
    hrv_7d_avg          FLOAT,    -- rolling average, computed at insert time
    hrv_30d_avg         FLOAT,
    hrv_baseline_ratio  FLOAT,    -- today / 30d avg
    sleep_7d_avg        FLOAT,
    weight_30d_trend    FLOAT,    -- slope of linear regression over 30d
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, metric_date)
);

-- This table is the primary target for:
-- - The Rules Engine (reads clean columns, no pivots)
-- - The Anomaly Detection Engine
-- - The text-to-SQL query frontend
-- - Charting and dashboards
-- The biometrics EAV table is the ingestion source;
-- daily_health_metrics is what you query.

-- -------------------------------------------------------
-- DAILY FEATURES (computed training load + readiness)
-- One row per user per day. Persisted so anomaly detection,
-- future ML, and the query frontend all use the same numbers.
-- -------------------------------------------------------
CREATE TABLE daily_features (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES auth.users(id),
    feature_date            DATE NOT NULL,
    -- Cardio load
    cardio_sessions_7d      INT,
    cardio_minutes_7d       FLOAT,
    cardio_minutes_14d      FLOAT,
    cardio_trimp_7d         FLOAT,    -- Training Impulse (HR-based load score)
    acute_chronic_ratio     FLOAT,    -- 7d avg / 28d avg load (injury risk signal)
    -- Strength load
    strength_sessions_7d    INT,
    strength_hard_sets_7d   INT,      -- sets at RPE >= 7
    strength_tonnage_7d     FLOAT,    -- US short tons: sum(reps*weight_lbs)/2000 (7d window)
    upper_body_sets_7d      INT,
    lower_body_sets_7d      INT,
    push_sets_7d            INT,
    pull_sets_7d            INT,
    -- Readiness
    sleep_debt_7d           FLOAT,    -- hours below target, cumulative 7d
    hrv_suppressed_days     INT,      -- consecutive days below baseline threshold
    -- Derived
    overall_readiness_score FLOAT,    -- simple composite 0-100, rule-based
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, feature_date)
);

-- -------------------------------------------------------
-- INTERVENTIONS (life events that affect health data)
-- One of the highest-value tables in the system.
-- Lets you later ask: "what changed before my HRV improved?"
-- -------------------------------------------------------
CREATE TABLE interventions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    event_date      DATE NOT NULL,
    category        TEXT NOT NULL,    -- 'supplement', 'medication', 'program',
                                      -- 'lifestyle', 'injury', 'travel', 'illness'
    description     TEXT NOT NULL,    -- "Started creatine 5g/day"
    is_ongoing      BOOLEAN DEFAULT TRUE,
    end_date        DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Example rows:
-- "Started creatine 5g/day"         category: supplement
-- "Changed to upper/lower split"    category: program
-- "Started statin medication"       category: medication
-- "Vacation — no structured training" category: lifestyle
-- "Left knee tendinopathy"          category: injury
-- "Work deadline week — poor sleep" category: lifestyle

-- -------------------------------------------------------
-- DAILY BRIEFINGS (persisted pipeline output)
-- -------------------------------------------------------
CREATE TABLE daily_briefings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES auth.users(id),
    briefing_date  DATE NOT NULL,
    flags          TEXT[],
    recommendations JSONB,            -- structured recommendations with confidence
    features_json  JSONB,             -- snapshot of daily_features at briefing time
    anomalies      JSONB,
    coaching_note  TEXT NOT NULL,
    model_used     TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, briefing_date)
);

-- -------------------------------------------------------
-- ANOMALY LOG
-- -------------------------------------------------------
CREATE TABLE anomaly_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    detected_date   DATE NOT NULL,
    metric          TEXT,
    anomaly_type    TEXT NOT NULL,    -- 'statistical', 'llm_pattern', 'cross_metric'
    description     TEXT NOT NULL,
    severity        TEXT,             -- 'low', 'medium', 'high'
    context_json    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- ROW-LEVEL SECURITY
-- -------------------------------------------------------
ALTER TABLE strength_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE cardio_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE biometrics            ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_health_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_features        ENABLE ROW LEVEL SECURITY;
ALTER TABLE interventions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_briefings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomaly_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings         ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON strength_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON cardio_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON biometrics
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_health_metrics
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_features
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON interventions
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_briefings
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON anomaly_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON user_settings
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- -------------------------------------------------------
-- INDEXES
-- -------------------------------------------------------
CREATE INDEX idx_strength_user_date       ON strength_events(user_id, event_date DESC);
CREATE INDEX idx_strength_exercise        ON strength_events(user_id, exercise_name, event_date DESC);
CREATE INDEX idx_strength_muscle          ON strength_events(user_id, muscle_group, event_date DESC);
CREATE INDEX idx_cardio_user_date         ON cardio_events(user_id, event_date DESC);
CREATE INDEX idx_cardio_type_date         ON cardio_events(user_id, activity_type, event_date DESC);
CREATE INDEX idx_biometrics_metric        ON biometrics(user_id, metric, event_date DESC);
CREATE INDEX idx_daily_metrics_date       ON daily_health_metrics(user_id, metric_date DESC);
CREATE INDEX idx_daily_features_date      ON daily_features(user_id, feature_date DESC);
CREATE INDEX idx_interventions_date       ON interventions(user_id, event_date DESC);
CREATE INDEX idx_briefings_user_date      ON daily_briefings(user_id, briefing_date DESC);
CREATE INDEX idx_anomalies_user_date      ON anomaly_events(user_id, detected_date DESC);
```

### Why This Schema Survives Service Changes

If a service disappears or you switch apps, only the ETL adapter changes:
- Hevy → Strong: write `strong_adapter.py` mapping to `strength_events`. Zero schema migration.
- Renpho → Withings: write `withings_adapter.py` writing the same `biometrics` rows with `source='withings'`.
- Google Health changes: update `google_health_adapter.py` only.

### ETL Adapter Pattern

```python
# adapters/hevy.py
def fetch_and_normalize(user_id: str, secrets: dict, since_date: date) -> list[dict]:
    """Returns list of strength_events rows — same shape regardless of source."""
    ...
    return [{
        "user_id":       user_id,
        "source":        "hevy",
        "source_id":     f"hevy:{workout_id}:{exercise_index}:{set_index}",
        "event_date":    workout_date,
        "exercise_name": normalize_exercise_name(raw_name),
        "muscle_group":  EXERCISE_MUSCLE_MAP.get(raw_name),
        "reps":          s.get("reps"),
        "weight_lbs":    kg_to_lbs(s.get("weight_kg")),
        "rpe":           s.get("rpe"),
        "set_type":      "working",
    }, ...]

# adapters/strong.py  (hypothetical future replacement)
def fetch_and_normalize(user_id: str, secrets: dict, since_date: date) -> list[dict]:
    ...
    return [{"user_id": user_id, "source": "strong", ...}]
```

### Deduplication Strategy

- `UNIQUE (user_id, source_id)` prevents re-inserting the same event
- Cross-source duplicates (Strava run + Apple Health copy): pick a **source priority** per type
  - Strength: Hevy is authoritative → ignore Apple Health "Strength Training" events
  - Cardio: Strava is authoritative for GPS → ignore Apple Health duplicates (match on date + duration ±5 min)
  - Biometrics: Apple Health for HRV/HR/sleep; Renpho for body comp

---

## Data Retention Policy

**No archival. Keep everything hot in Supabase.**

One user's entire structured health history — workouts, biometrics, briefings, features — accumulates at roughly 5-15 MB/year. After 10 years that's 50-150 MB, well within the 500 MB Supabase free tier. The complexity of Parquet archival, S3 lifecycle policies, and a secondary query target (Athena/DuckDB) is not justified at this scale.

Raw JSON files in S3 (`/raw/` prefix) are kept forever under S3 Intelligent-Tiering. AWS automatically moves them to cheaper storage tiers as they age — no lifecycle rules needed, and cost approaches zero within months.

If you ever hit the Supabase free tier limit (years away), upgrade to Supabase Pro ($25/mo) before considering archival complexity.

**Storage cost summary:**

| Layer | Service | Est. cost after 5 years |
|---|---|---|
| Normalized events + features | Supabase Free | $0 |
| Raw JSON files (~100MB/year) | S3 Intelligent-Tiering | ~$0.02/mo |
| Total | | **~$0.02/mo** |

---

---

## Rules Engine — Approaches & Alternatives

You raised a real tension: you don't want to hand-code every rule, threshold, and metric combination. Maintaining a growing list of `if` statements is tedious, brittle, and limited to what you thought of when you wrote it. Here are the viable approaches, with honest trade-offs.

---

### Option A: Hand-Coded Rules (Current Design)

What's in the doc now. Python `if` statements, thresholds in SSM Parameter Store.

**Pros:** Deterministic, debuggable, cheap, fast. When a flag fires you know exactly why.

**Cons:** You define every rule. You choose every threshold. You don't discover anything you didn't already know to look for. Scales poorly past ~20 rules — becomes a maintenance burden.

**Best for:** Phase 1. Start here. The first 6-8 rules (HRV threshold, cardio frequency, consecutive training days, sleep minimum) cover the most important signals and are worth the explicitness.

---

### Option B: Feature Store + LLM Synthesis (No Rules Engine)

Skip rule evaluation entirely. Instead of pre-computing flags, send the `daily_features` and `daily_health_metrics` rows directly to the LLM as a structured JSON context package. Let the LLM do all interpretation.

```python
prompt = f"""
Here is today's health data for {user_name}:

{json.dumps(daily_features_row, indent=2)}
{json.dumps(daily_health_metrics_row, indent=2)}

Recent anomalies detected (statistical):
{json.dumps(anomaly_list, indent=2)}

User goals and expert principles:
{goals}
{principles}

Write a morning coaching briefing. Identify what needs attention today,
give one specific recommendation, and cite which goal or principle it
relates to. Be direct. 5-8 sentences.
"""
```

**Pros:** No rules to write or maintain. The LLM can reason across all metrics simultaneously and notice relationships you didn't code. More adaptive.

**Cons:** LLMs are inconsistent — the same data might produce different recommendations on different days. You lose the audit trail ("why did it say that?"). LLM may hallucinate reasoning even with real numbers in context. Costs slightly more per call (more tokens). Harder to debug when output is wrong.

**Verdict:** Viable with Sonnet; risky with Haiku. Good for the **weekly deep analysis** where you want exploratory insight. Probably too loose for the daily briefing where consistency matters.

---

### Option C: Hybrid — Thin Rules + LLM Interpretation (Recommended)

A small set of deterministic rules produce **structured recommendations with confidence scores**, not just binary flags. The LLM receives these plus the raw feature values and interprets them. The rules handle "what's important today"; the LLM handles "why and what to do about it."

```python
# Rules produce structured recommendations, not just flags
recommendations = [
    {
        "type": "reduce_training_load",
        "confidence": 0.88,
        "reason": "HRV 18% below 30d baseline, 3rd consecutive suppressed day",
        "data": {"hrv_ratio": 0.82, "suppressed_days": 3}
    },
    {
        "type": "prioritize_cardio",
        "confidence": 0.74,
        "reason": "No cardio in 6 days; LDL goal requires 3 sessions/week",
        "data": {"last_cardio_days": 6, "weekly_cardio_count": 0}
    }
]
```

The LLM's prompt becomes:

```python
prompt = f"""
Today's key data:
- Sleep: {dhm.sleep_hours:.1f} hrs  
- HRV: {dhm.hrv_baseline_ratio:.0%} of baseline
- Training load (7d): {df.strength_hard_sets_7d} hard sets, {df.cardio_minutes_7d:.0f} min cardio
- Free time today: {calendar_blocks}

Pre-computed recommendations:
{json.dumps(recommendations, indent=2)}

Goals: {goals}
Principles: {principles}

Write a morning coaching briefing. Narrate the recommendations — don't just repeat them.
Explain the reasoning in plain language, cite the goal or principle driving each one,
and give one concrete action for today.
"""
```

**Pros:** Deterministic logic handles thresholds (so you can always explain a recommendation). LLM handles tone, synthesis, and cross-recommendation coherence. Smaller rule set needed — rules produce recommendations, not a laundry list of flags. Confidence scores let you tune signal strength.

**Cons:** Still requires some rule definition. More complex data model than pure flags.

**Verdict:** The right long-term architecture. Implement Option A first, migrate to Option C once you have 2-3 weeks of real output to learn from.

---

### Option D: Statistical Feature Analysis (No Rules, No LLM for Logic)

Use proper statistical methods on the `daily_features` store to surface what's anomalous, trending, or correlated. Feed the statistical output — not raw data — to the LLM.

Methods:
- **Z-score on rolling window**: already in the anomaly engine — flag values >2 stdev from 30d baseline
- **Linear regression over 14d**: detect gradual trends (weight creeping up, VO2 max declining)
- **Acute:chronic workload ratio**: cardio load (7d avg / 28d avg) — standard injury prevention metric
- **Lagged correlation**: sleep quality on day N vs. workout volume on day N+1 — computationally cheap
- **Changepoint detection**: identify when a metric regime shifted (e.g., HRV went from averaging 52 to averaging 44 three weeks ago)

The output is a structured summary of what the statistics found — identical in shape to Option C's recommendations. The LLM narrates it.

**Pros:** Discovers patterns you didn't define. Works on any metric automatically. Improves as data accumulates. No threshold tuning.

**Cons:** Statistical methods have their own false positive rates. Z-score is naive — it doesn't understand that a hard training week should suppress HRV. Requires more upfront data engineering.

**Verdict:** This is the right direction for the anomaly engine (already partially implemented). Could eventually replace most hand-coded rules as the feature store matures.

---

### Recommended Evolution Path

```
Phase 1: Option A (hand-coded rules, SSM thresholds)
         → Learn what matters from real data
         → Fast to build, easy to debug

Phase 2: Add Option D (statistical analysis on daily_features)
         → Surface patterns rules don't catch
         → Supplement rules output, don't replace it yet

Phase 3: Migrate to Option C (structured recommendations + confidence)
         → Rules become recommendation generators
         → LLM gets richer, more structured context
         → Statistical anomalies feed into same recommendation format

Phase 4 (optional): Reduce hand-coded rules progressively
         → Once statistical + correlation analysis covers most signals,
           retire rules one by one in favor of data-driven detection
```

The key insight from the feedback: **the feature store (`daily_features`) is the most important long-term investment**. Once you're computing and persisting training load, rolling HRV averages, acute:chronic ratio, sleep debt, and body comp trends every day, you can run any analysis against that — statistical, LLM, or otherwise — without re-querying raw event tables. Build the feature store well in Phase 1; the intelligence layer above it can evolve freely.

---

All thresholds are SSM standard parameters — free, and editable from the AWS console with no redeploy.

```
Path structure: /soma/{env}/{user_id}/rules/

/soma/prod/{user_id}/rules/cardio_gap_flag_days          7
/soma/prod/{user_id}/rules/cardio_weekly_min             3
/soma/prod/{user_id}/rules/strength_weekly_goal          4
/soma/prod/{user_id}/rules/consecutive_strength_max      2
/soma/prod/{user_id}/rules/hrv_recovery_threshold        0.85
/soma/prod/{user_id}/rules/hrv_baseline_days             30
/soma/prod/{user_id}/rules/sleep_minimum_hours           6.5
/soma/prod/{user_id}/rules/weight_trend_flag_lbs         2.0
/soma/prod/{user_id}/rules/deload_consecutive_suppress   3
```

To change a threshold: AWS Console → SSM Parameter Store → edit value. Next Lambda run picks it up with no code change.

```python
def load_thresholds(env: str, user_id: str) -> dict:
    ssm = boto3.client("ssm")
    response = ssm.get_parameters_by_path(
        Path=f"/soma/{env}/{user_id}/rules/"
    )
    return {p["Name"].split("/")[-1]: float(p["Value"]) for p in response["Parameters"]}

def compute_daily_context(db, user_id: str, env: str) -> dict:
    t = load_thresholds(env, user_id)
    metrics = {}
    flags = []
    today = date.today()

    # --- Cardio ---
    last_cardio_date = db.query_scalar(
        "SELECT MAX(event_date) FROM cardio_events WHERE user_id = %s", user_id
    )
    metrics["last_cardio_days"] = (today - last_cardio_date).days if last_cardio_date else 999
    metrics["cardio_sessions_this_week"] = db.query_scalar(
        "SELECT COUNT(DISTINCT event_date) FROM cardio_events "
        "WHERE user_id = %s AND event_date >= CURRENT_DATE - INTERVAL '7 days'", user_id
    )
    if metrics["last_cardio_days"] > int(t["cardio_gap_flag_days"]):
        flags.append("cardio_missing")
    if metrics["cardio_sessions_this_week"] < int(t["cardio_weekly_min"]):
        flags.append("cardio_below_goal")

    # --- Strength ---
    metrics["strength_sessions_this_week"] = db.query_scalar(
        "SELECT COUNT(DISTINCT event_date) FROM strength_events "
        "WHERE user_id = %s AND event_date >= CURRENT_DATE - INTERVAL '7 days'", user_id
    )
    metrics["consecutive_strength_days"] = db.query_scalar(
        "SELECT COUNT(DISTINCT event_date) FROM strength_events "
        "WHERE user_id = %s AND event_date >= CURRENT_DATE - INTERVAL '2 days'", user_id
    )
    if metrics["strength_sessions_this_week"] >= int(t["strength_weekly_goal"]):
        flags.append("strength_goal_met")
    if metrics["consecutive_strength_days"] >= int(t["consecutive_strength_max"]):
        flags.append("consecutive_strength_days")

    # --- Recovery ---
    hrv_today = db.query_scalar(
        "SELECT value FROM biometrics WHERE user_id = %s AND metric='hrv_rmssd' "
        "ORDER BY event_date DESC LIMIT 1", user_id
    )
    hrv_baseline = db.query_scalar(
        "SELECT AVG(value) FROM biometrics WHERE user_id = %s AND metric='hrv_rmssd' "
        "AND event_date >= CURRENT_DATE - INTERVAL '%s days'",
        user_id, int(t["hrv_baseline_days"])
    )
    if hrv_today and hrv_baseline:
        metrics["hrv_pct_of_baseline"] = round(hrv_today / hrv_baseline, 2)
        if metrics["hrv_pct_of_baseline"] < t["hrv_recovery_threshold"]:
            flags.append("recovery_compromised")

    metrics["sleep_hours"] = db.query_scalar(
        "SELECT value FROM biometrics WHERE user_id = %s AND metric='sleep_hours' "
        "ORDER BY event_date DESC LIMIT 1", user_id
    ) or 0.0
    if metrics["sleep_hours"] < t["sleep_minimum_hours"]:
        flags.append("sleep_deficit")

    if "recovery_compromised" in flags and "consecutive_strength_days" in flags:
        flags.append("recommend_rest_day")

    # --- Calendar ---
    metrics["free_blocks_today"] = get_calendar_free_blocks(user_id)

    return {"flags": flags, "metrics": metrics}
```

---

## Anomaly Detection Engine

The Rules Engine catches what you've explicitly coded. The Anomaly Detection Engine catches everything else — unexpected patterns, surprising correlations, gradual drifts you didn't know to look for.

This runs alongside the Rules Engine on the same daily schedule. Its output is included in the coaching prompt and persisted to `anomaly_events`.

### Two-Layer Approach

**Layer 1: Statistical anomaly detection (deterministic, cheap)**

Z-score and rolling window analysis over every biometric metric. No LLM involved — pure math.

```python
def detect_statistical_anomalies(db, user_id: str, window_days: int = 30) -> list[dict]:
    anomalies = []
    metrics_to_check = [
        "hrv_rmssd", "resting_hr", "sleep_hours", "steps",
        "body_weight_lbs", "body_fat_pct", "vo2_max"
    ]

    for metric in metrics_to_check:
        rows = db.query(
            "SELECT event_date, value FROM biometrics "
            "WHERE user_id = %s AND metric = %s "
            "AND event_date >= CURRENT_DATE - INTERVAL '%s days' "
            "ORDER BY event_date DESC",
            user_id, metric, window_days
        )
        if len(rows) < 7:
            continue  # not enough data

        values = [r["value"] for r in rows]
        today_val = values[0]
        mean = statistics.mean(values[1:])   # baseline excludes today
        stdev = statistics.stdev(values[1:])
        if stdev == 0:
            continue

        z = (today_val - mean) / stdev

        if abs(z) > 2.5:  # >2.5 standard deviations from recent baseline
            anomalies.append({
                "user_id":       user_id,
                "detected_date": date.today().isoformat(),
                "metric":        metric,
                "anomaly_type":  "statistical",
                "severity":      "high" if abs(z) > 3.0 else "medium",
                "description":   f"{metric} is {abs(z):.1f} standard deviations "
                                 f"{'above' if z > 0 else 'below'} your recent baseline "
                                 f"({today_val:.1f} vs avg {mean:.1f})",
                "context_json":  {"z_score": z, "today": today_val, "baseline_mean": mean,
                                  "baseline_stdev": stdev, "window_days": window_days}
            })

        # Also check for gradual trend (consistent direction over 14 days)
        if len(values) >= 14:
            recent = values[:7]
            older = values[7:14]
            if statistics.mean(recent) < statistics.mean(older) * 0.90:
                anomalies.append({
                    "user_id":       user_id,
                    "detected_date": date.today().isoformat(),
                    "metric":        metric,
                    "anomaly_type":  "statistical",
                    "severity":      "low",
                    "description":   f"{metric} has declined ~10%+ over the past 2 weeks",
                    "context_json":  {"recent_7d_avg": statistics.mean(recent),
                                      "prior_7d_avg": statistics.mean(older)}
                })

    return anomalies
```

**Layer 2: LLM-assisted pattern detection (weekly, not daily)**

Once a week (Sunday), send a summary of recent metrics to Claude Sonnet and ask it to surface patterns that aren't captured by statistical anomalies — cross-metric correlations, seasonality, narrative patterns.

```python
def detect_llm_anomalies(db, user_id: str) -> list[dict]:
    """Runs weekly (Sunday). Uses Sonnet — more expensive, used sparingly."""

    # Pull 60 days of summary data
    summary = build_weekly_summary(db, user_id, days=60)

    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": f"""
You are analyzing health and fitness data. Identify any notable patterns,
correlations, or anomalies that might not be captured by simple threshold checks.

Focus on:
- Cross-metric correlations (e.g. sleep quality vs next-day HRV vs workout performance)
- Gradual trends that wouldn't trigger a single-day alert
- Patterns related to training periodization
- Anything surprising or worth attention

Data summary (last 60 days):
{summary}

Return a JSON array. Each item: {{"pattern": "...", "severity": "low|medium|high",
"recommendation": "..."}}. Return only JSON, no other text.
"""}]
    )

    patterns = json.loads(response.content[0].text)
    return [{
        "user_id":       user_id,
        "detected_date": date.today().isoformat(),
        "anomaly_type":  "llm_pattern",
        "severity":      p["severity"],
        "description":   p["pattern"],
        "context_json":  {"recommendation": p["recommendation"]}
    } for p in patterns]
```

### Integration with Daily Briefing

Statistical anomalies (layer 1) are included in every daily briefing if present. LLM anomalies (layer 2) are included in the Sunday weekly summary. Both are persisted to `anomaly_events` for historical review.

```python
# In daily briefing Lambda
context = compute_daily_context(db, user_id, env)
stat_anomalies = detect_statistical_anomalies(db, user_id)

# Persist anomalies
if stat_anomalies:
    db.insert_many("anomaly_events", stat_anomalies)

# Include in prompt
anomaly_text = "\n".join(f"- {a['description']}" for a in stat_anomalies) or "None today"
```

### What This Catches That Rules Don't

- VO2 max quietly declining over 3 weeks while everything else looks normal
- Body weight trending up 0.3 lbs/week for a month — individually unremarkable, cumulatively significant
- Sleep duration normal but sleep quality (deep/REM ratio) degrading
- Resting HR creeping up week-over-week indicating accumulated fatigue
- A correlation you never coded: "your best bench sessions follow nights with 7.5+ hours sleep"

---

## Persisting Daily Pipeline Output

Both the Rules Engine output and the LLM coaching note are saved to `daily_briefings` after every run. This enables:

- "What was my coaching note on the day I hit a PR?" 
- Trend analysis on flags over time: "How often did `recovery_compromised` fire this year?"
- Review past briefings in the query frontend
- Fine-tuning guidelines by reviewing what the LLM said vs. what actually happened

```python
# In briefing Lambda, after generating coaching_note
db.upsert("daily_briefings", {
    "user_id":       user_id,
    "briefing_date": date.today().isoformat(),
    "flags":         context["flags"],
    "metrics":       context["metrics"],        # JSONB
    "anomalies":     [a["description"] for a in stat_anomalies],
    "coaching_note": coaching_note,
    "model_used":    "claude-haiku-4-5-20251001",
}, on_conflict="user_id, briefing_date")
```

---

## Rules Engine Thresholds in SSM Parameter Store

*(See Rules Engine section above for full SSM path structure and `load_thresholds()` implementation.)*

To change any rule threshold: AWS Console → Systems Manager → Parameter Store → `/soma/{env}/{user_id}/rules/` → edit value. No code change, no redeploy.

---

## Prompt Template & LLM Call

```python
def build_prompt(context: dict, anomalies: list, goals: str, principles: str) -> str:
    m = context["metrics"]
    f = context["flags"]
    anomaly_text = "\n".join(f"- {a['description']}" for a in anomalies) or "None today"

    return f"""
You are a personal fitness coach delivering a morning briefing.

Today's data:
- Sleep: {m['sleep_hours']} hours
- HRV: {m.get('hrv_pct_of_baseline', 1.0) * 100:.0f}% of 30-day baseline
- Strength sessions this week: {m['strength_sessions_this_week']}
- Cardio sessions this week: {m['cardio_sessions_this_week']}
- Last cardio: {m['last_cardio_days']} days ago
- Free time blocks today: {', '.join(m['free_blocks_today']) or 'none found'}

Active flags: {', '.join(f) if f else 'none'}

Statistical anomalies detected:
{anomaly_text}

--- PERSONAL GOALS (my-goals.md) ---
{goals}

--- INJURY HISTORY (injury-history.md) ---
{injury_history}

--- EXPERT PRINCIPLES (expert-principles.md) ---
{principles}

Write a concise coaching note (5-8 sentences). Be direct. Lead with recovery status,
then give one specific recommendation for today with rationale. When drawing on a
specific goal or principle, name it briefly — e.g. "(per your LDL goal)" or
"(per MEV/MAV)". If there are notable anomalies, mention the most significant one.
Reference the calendar if relevant. Narrate — don't restate all the numbers.
"""
```

---

## Guidelines Files

Three markdown files in S3, all injected into every LLM prompt (briefing and, when wired, coaching chat). LLM cites the source when drawing on any of them.

### `guidelines/{user_id}/my-goals.md` — Personal Goals & Context

Your goals, schedule, health flags, upcoming events. Edit whenever goals change — just re-upload to S3, no deployment needed.

```markdown
# My Personal Health Goals

## Primary Goals (priority order)
1. Reduce LDL cholesterol through consistent aerobic exercise
2. Build strength in compound lifts (squat, deadlift, bench, overhead press)
3. Complete the Mountain Viking biking trip in September

## Training Schedule (typical)
- Strength: 3-4x/week
- Cardio (run or bike): 2-4x/week
- Rest: at least 1 full rest day/week

## Minimums I want enforced
- Cardio: at least 3 sessions/week for cardiovascular health
- No more than 2 consecutive hard training days
- Deload if HRV suppressed >3 consecutive days

## Current Health Context
- Elevated LDL — aerobic exercise is a primary intervention
- [Medications or non-injury health flags — see injury-history.md for injuries]

## Upcoming Events
- Mountain Viking biking trip: September 2026
```

### `guidelines/{user_id}/injury-history.md` — Injury History & Movement Constraints

Past and current injuries the coach must respect. Edit when status changes (flare, cleared, new limitation) — re-upload to S3, no deployment needed. The LLM should **not** recommend loading patterns that conflict with active entries here.

```markdown
# Injury History

## Active / limiting (as of YYYY-MM-DD)
- **Left shoulder impingement** (since 2025-11): avoid overhead pressing > moderate load; prefer neutral-grip pressing; stop if sharp pain in bottom third of ROM.
- **Right Achilles tendinopathy** (since 2026-03): cap weekly running volume increases at 10%; no hill sprints until cleared.

## Resolved / watch list
- **Lumbar strain** (2024-08, resolved): deadlift from blocks if morning stiffness > usual.

## General constraints
- No max-effort singles on lower back within 48h of long bike days.
- Deload upper push if shoulder symptoms return for 2+ sessions.

## Notes for the coach
- Prefer substituting movements over pushing through joint pain.
- When ACWR or load flags fire, cross-check against active injuries before recommending intensity increases.
```

### `guidelines/{user_id}/expert-principles.md` — Expert Training Principles

Distilled from exercise scientists. Stable, changes rarely. Populated by pasting YouTube transcripts into Claude.

```markdown
# Expert Training Principles

## Volume Landmarks (Dr. Mike Israetel / RP Strength)
- MEV: ~10 sets/muscle group/week to maintain
- MAV: 12–20 sets/week for hypertrophy
- MRV: do not exceed or recovery degrades
- Deload when: performance stalls 2+ weeks, or HRV chronically suppressed

## Cardiovascular Health (Huberman, Attia)
- Zone 2 (~60-70% max HR): most effective for metabolic/cardiovascular health
- Minimum 150 min moderate / 75 min vigorous per week for LDL benefit
- VO2 max intervals (1-2x/week) add longevity benefit

## Recovery Principles
- HRV is the most reliable daily readiness marker
- >15% below 30-day baseline = compromised recovery, reduce intensity
- Sleep is the highest-leverage recovery tool

## Strength Programming (Nippard, Israetel)
- Progressive overload every 1-2 weeks
- Deload: reduce volume 40-50%, keep intensity
- Creatine: 3-5g/day, no loading needed

## Injury Prevention
- Running: don't increase weekly mileage >10% week-over-week
- Strength: avoid training the same movement pattern to failure 2 consecutive days
```

---

## Natural Language Query Frontend (Future Phase)

A dedicated interface — web or iOS — where you type a question and get a real answer from your actual data.

Example queries:
- "How does bench press trend correlate with sleep quality over 3 months?"
- "Show running mileage by month for the past year"
- "What were my best recovery weeks and what was different?"
- "When did I last deadlift over 225?"
- "What flags came up most often in my briefings this year?"
- "Show me any anomalies detected in the last 30 days"

### Architecture

No schema changes needed. The database tables that exist today are exactly what a text-to-SQL system queries. The `daily_briefings` and `anomaly_events` tables are also queryable, so you can ask about coaching history and past anomalies.

```
User types question
        │
  Lambda endpoint
        │
   ┌────┴────┐
Claude     Supabase
(text-to-SQL  (executes
 with schema   the query)
 context)
   └────┬────┘
        │
  Table + chart rendered
```

The LLM sees only your schema — not your data. Data never leaves Supabase during query generation.

### Frontend Options (in order of effort)

| Option | Effort | Cost | Notes |
|---|---|---|---|
| **Streamlit** | 1 day | Free | Good for initial validation; not polished |
| **Next.js PWA on Vercel** | 1-2 weekends | Free | Clean UI, iPhone home screen installable |
| **Native SwiftUI iOS** | 2-3 weekends | Free (TestFlight) | Best native experience, push notifications |

Recommended progression: Streamlit first to validate you'd actually use it, then Next.js PWA.

---

## Hosting: Serverless Stack

No always-on server. The pipeline is entirely ephemeral compute triggered on schedule.

### The Stack

| Component | Service | Cost/mo |
|---|---|---|
| Scheduler | AWS EventBridge | ~$0 |
| Compute | AWS Lambda (~150 invocations/mo) | ~$0 |
| Database (permanent, no archival) | Supabase Free | Free |
| Raw JSON storage | AWS S3 Intelligent-Tiering | ~$0.02 |
| Email delivery | AWS SES | ~$0 |
| API credentials | AWS Secrets Manager (~4 secrets) | ~$0.40 |
| Rule thresholds | AWS SSM Parameter Store (standard) | Free |
| Guidelines + schema files | AWS S3 | ~$0 |
| LLM — daily briefing (Haiku) | Anthropic API | ~$0.50 |
| LLM — weekly anomaly scan (Sonnet) | Anthropic API | ~$0.50 |
| LLM — ad-hoc queries | Anthropic API | ~$1-3 |
| **Total** | | **~$2-4/mo** |

### Daily Pipeline Flow

```
5:50 AM  →  EventBridge → Lambda: etl_job (all sources)
             └─ Fetch from each API
             └─ Write raw JSON to S3 /raw/{user_id}/{source}/{date}/
             └─ Normalize → upsert to strength_events, cardio_events, biometrics
             └─ Pivot biometrics → upsert daily_health_metrics (wide row)

5:55 AM  →  EventBridge → Lambda: features_job
             └─ Compute daily_features (training load, rolling windows, readiness)
             └─ Run statistical anomaly detection vs daily_health_metrics
             └─ Poll CalDAV for today's free blocks

6:00 AM  →  EventBridge → Lambda: briefing_job
             └─ Build context from daily_features + anomalies + guidelines
             └─ Call Claude Haiku
             └─ Save to daily_briefings
             └─ Send via SES email

Sundays  →  EventBridge → Lambda: weekly_anomaly_job
             └─ Build 60-day weekly summary (aggregated, not raw rows)
             └─ Call Claude Sonnet for cross-metric pattern analysis
             └─ Save anomalies to anomaly_events
             └─ Include in Sunday briefing
```

---

## Notifications

### Email via AWS SES (Primary)

One email per day, plain text or lightly formatted HTML. Archivable and searchable on mobile.

### Telegram (Optional)

Create a bot via BotFather. Store token + chat ID in Secrets Manager. One `requests.post()` call. Free.

---

## Phased Build Plan

### Phase 0 — Local Dev Environment (Day 1)
- Install Docker, start local Postgres + LocalStack
- Apply schema to local Postgres
- Seed synthetic data with `seed_local.py`
- Run pipeline components locally against seed data
- Validate output before touching any cloud service

### Phase 1 — Core Loop, Staging (Weekend 1)
- Set up AWS account + Supabase staging project
- Deploy schema to Supabase staging
- Build Hevy + Strava ETL adapters; validate against real API responses
- Write Rules Engine skeleton (3-4 rules); validate flags against seed data
- Wire EventBridge → Lambda → briefing → SES email (staging)
- Let staging run 2-3 days; confirm briefing quality

### Phase 2 — Production + Full Rules (Weekend 2)
- Promote to production; set up Supabase prod project
- Add Apple Health Auto Export → webhook → Lambda ETL
- Expand Rules Engine to full rule set
- Populate `my-goals.md`, `injury-history.md`, and `expert-principles.md`, upload to S3
- First real production briefing

### Phase 3 — Full Data Sources (Weekend 3)
- Add Google Health API ingestion (before Sept 2026 Fitbit sunset)
- Add Renpho ingestion via `renpho-api`
- Add iCloud CalDAV poll for free block detection
- Refine rules and prompt based on 2 weeks of real briefings

### Phase 4 — Anomaly Detection + Long-Term Storage (Weekend 4)
- Build statistical anomaly detection; integrate into daily briefing
- Build weekly LLM anomaly scan
- Build nightly Parquet archive Lambda → S3
- Set S3 lifecycle policies
- One-time NRC historical import → Parquet cold archive

### Phase 5 — Query Frontend: Streamlit (1 day)
- Streamlit: natural language → text-to-SQL → Supabase → table/chart
- Queries span all tables including `daily_briefings` and `anomaly_events`
- Validate concept before investing in polished UI

### Phase 6 — Query Frontend: Web App (1-2 weekends)
- Next.js on Vercel: clean UI, chart rendering
- Lambda text-to-SQL API endpoint
- Supabase Auth (protects data even as a single user)
- PWA manifest → installable on iPhone home screen

### Phase 7 — Native iOS App (optional, 2-3 weekends)
- SwiftUI: text input, results, charts
- Same Lambda API as web app
- Face ID auth, push notifications (can replace SES email)
- TestFlight distribution

---

## Implementation Guide

The philosophy: validate data locally before writing cloud code. Every API returns something slightly different from what the docs suggest.

---

### Step 0 — Prerequisites & Accounts

1. **Docker Desktop** — for local Postgres + LocalStack
2. **AWS account** — set a billing alert at $10/month immediately
3. **Supabase account** — create two projects: `soma-staging`, `soma-prod`
4. **Anthropic API key** — set a usage limit at `console.anthropic.com`
5. **Hevy API key** — `hevy.com/settings → Developer`
6. **Strava API app** — `strava.com/settings/api`, note client ID + secret
7. **Apple ID app-specific password** — `appleid.apple.com → Security` (for CalDAV)
8. **Google Cloud project** — for Google Health API OAuth2
9. **Renpho credentials** — email/password, used by `renpho-api`
10. **Private GitHub repo** — code only; no secrets in repo

Store every credential in **AWS Secrets Manager**, scoped by environment and user ID.

---

### Step 1 — Local Environment Setup

```bash
# Start local Postgres
docker run -d --name soma-local \
  -e POSTGRES_PASSWORD=localpass -e POSTGRES_DB=soma \
  -p 5432:5432 postgres:16

# Apply schema
psql -h localhost -U postgres -d soma -f schema.sql

# Start LocalStack
docker run -d --name localstack \
  -e SERVICES=s3,ssm,secretsmanager,ses \
  -p 4566:4566 localstack/localstack

# Seed SSM thresholds
aws --endpoint-url=http://localhost:4566 ssm put-parameter \
  --name "/soma/local/local-dev-user/rules/cardio_weekly_min" \
  --value "3" --type String

# Seed the database with synthetic data
ENV=local python scripts/seed_local.py --user-id local-dev-user

# Validate: query seed data
psql -h localhost -U postgres -d soma \
  -c "SELECT metric, COUNT(*), AVG(value) FROM biometrics GROUP BY 1"
```

---

### Step 2 — API Exploration & Data Validation

Before writing any ETL, call each API locally and inspect raw responses. Schema design follows actual data, not docs.

#### Hevy
```python
import requests, json
headers = {"api-key": "YOUR_HEVY_KEY"}
r = requests.get("https://api.hevyapp.com/v1/workouts?page=1&pageSize=5", headers=headers)
print(json.dumps(r.json(), indent=2))
```
Inspect: date field name + format, set nesting, weight unit, null handling for RPE/bodyweight exercises, pagination model.

#### Strava
```bash
# Manual OAuth exchange once
https://www.strava.com/oauth/authorize?client_id=YOUR_ID&response_type=code&redirect_uri=http://localhost&scope=activity:read_all
```
Inspect: `type` field values for your activities, distance unit (meters), `average_heartrate` presence, `start_date` vs `start_date_local`.

#### Apple Health (webhook.site)
Point Health Auto Export to `https://webhook.site/your-id`. Inspect the payload envelope, sleep representation (stages vs. summary), HRV type (SDNN vs. RMSSD), duplicate workout handling.

#### Renpho
```python
from renpho import RenphoClient
client = RenphoClient(email="...", password="...")
client.auth()
print(json.dumps(client.get_measurements(limit=3), indent=2))
```
Inspect: exact field names, units, timestamp format.

#### iCloud CalDAV
```python
import caldav
client = caldav.DAVClient(url="https://caldav.icloud.com",
    username="you@icloud.com", password="APP_SPECIFIC_PASSWORD")
cals = client.principal().calendars()
```
Inspect: calendar list, VEVENT structure, DTSTART/DTEND format for all-day vs timed events.

**Validation checklist per source:**
- [ ] Raw response shape documented
- [ ] Date/timestamp format confirmed and parsed correctly
- [ ] Null handling for optional fields confirmed
- [ ] Unit normalization plan documented (kg→lbs, meters→miles)
- [ ] Deduplication key identified

---

### Step 3 — Schema Validation (Local)

With real API responses in hand, apply the full schema to local Postgres and validate:

```bash
psql -h localhost -U postgres -d soma -f schema.sql
```

Manually insert 2-3 real rows from each API response:
```sql
-- Insert real Hevy data sample
INSERT INTO strength_events (user_id, source, source_id, event_date, exercise_name, set_number, reps, weight_lbs)
VALUES ('local-dev-user', 'hevy', 'hevy:a1b2c3d4-e5f6-7890-abcd-ef1234567890:0:1', '2026-06-01', 'Barbell Bench Press', 1, 8, 185.0);
```

**Validation checklist:**
- [ ] All tables accept real data without type errors
- [ ] `UNIQUE (user_id, source_id)` constraint catches duplicate inserts
- [ ] `UNIQUE (user_id, source, event_date, metric)` on biometrics works correctly
- [ ] Canonical metric names consistent across sources
- [ ] `SELECT exercise_name, COUNT(*) FROM strength_events GROUP BY 1` returns sensible results
- [ ] RLS policies don't break single-user local queries (disable RLS locally if needed)

---

### Step 4 — ETL Lambda (Hevy first, locally)

```bash
ENV=local python -m pipeline.etl --source hevy --user-id local-dev-user
```

Verify rows in local Postgres. Re-run — confirm no duplicate rows. Check row count vs. Hevy app history.

Add sources one at a time: Strava, then Apple Health, then Renpho, then Google Health.

---

### Step 5 — Rules Engine Validation (Local)

```bash
ENV=local python -m pipeline.rules --user-id local-dev-user
```

Expected output:
```
Flags: ['cardio_below_goal']
Metrics: {'last_cardio_days': 4, 'hrv_pct_of_baseline': 0.96, 'sleep_hours': 7.2, ...}
```

Test edge cases manually: insert a low HRV row, confirm `recovery_compromised` fires. Insert a row to make `last_cardio_days > 7`, confirm `cardio_missing` fires. Insert normal data, confirm no false positives.

---

### Step 6 — Anomaly Detection Validation (Local)

```bash
ENV=local python -m pipeline.anomaly --user-id local-dev-user
```

Artificially insert an anomalous HRV value (e.g., 3 stdev below mean) and confirm the statistical engine detects it. Review the description text — is it human-readable and accurate?

---

### Step 7 — LLM Call Validation (Local)

```bash
ENV=local python -m pipeline.briefing --user-id local-dev-user
# Prints to stdout, does not send email
```

Review output critically:
- Does it cite goals and principles correctly?
- Does it mention anomalies when present?
- Is it giving sensible advice given the flags?
- Is the tone right — direct, not hedgy?
- Is 5-8 sentences the right length?

Iterate on the prompt and guidelines files until output is genuinely useful across several simulated scenarios (good recovery, compromised recovery, cardio gap, anomaly detected).

---

### Step 8 — Staging Deployment & End-to-End Test

```bash
# Deploy to staging (from infrastructure/; stack id must match app.py)
cd infrastructure
cdk deploy SomaStagingStack

# Trigger ETL manually
aws lambda invoke --function-name soma-staging-etl_job response.json
```

Let staging run for 2-3 days. Verify:
- [ ] Rows landing in Supabase staging correctly
- [ ] Rules Engine producing sensible flags against real data
- [ ] Email arriving with `[STAGING]` prefix
- [ ] CloudWatch Logs show no errors
- [ ] Briefing content is coherent and useful

---

### Step 9 — Production Deployment

```bash
cd infrastructure
cdk deploy SomaProdStack
```

Monitor the first week of production briefings. Tune SSM thresholds as needed. Update guidelines files via S3 upload — no redeploy required.

---

### Step 10 — Ongoing Calibration

- Flags fire too often → raise SSM threshold
- Flags not firing → lower SSM threshold
- Briefing tone wrong → edit prompt in Lambda (requires deploy)
- Wrong principles cited → edit `expert-principles.md` in S3 (no deploy)
- Goal changes → edit `my-goals.md` in S3 (no deploy)
- Injury/limitation updates → edit `injury-history.md` in S3 (no deploy)

---

## Where Everything Lives

```
Repo
  infrastructure/  — AWS CDK v2 (Python): app.py registers SomaStagingStack, SomaProdStack

AWS
  EventBridge    — cron scheduling
  Lambda         — ETL (raw write + normalize), feature computation,
                   Rules Engine, anomaly detection,
                   briefing, text-to-SQL API
  SES            — outbound email
  S3             — /raw/{user_id}/{source}/{date}/ (permanent raw JSON)
                   /guidelines/{user_id}/ (my-goals.md, injury-history.md, expert-principles.md)
                   Lambda deployment packages
  Secrets Manager — API keys, tokens, passwords
  SSM Parameter   — rule thresholds (per env, per user)

Supabase
  PostgreSQL     — all normalized data, permanent (no archival)
                   strength_events · cardio_events · biometrics
                   daily_health_metrics · daily_features
                   interventions · daily_briefings · anomaly_events
  Auth           — user management, JWTs, RLS enforcement

Vercel (future)
  Next.js        — web query frontend / PWA

Anthropic API
  Haiku          — daily briefing, text-to-SQL
  Sonnet         — weekly LLM anomaly + pattern scan

Source APIs (outbound calls from Lambda)
  hevy.com · strava.com · googleapis.com
  renpho.com · caldav.icloud.com
```

---

## Complete Cost Estimate

| Item | Service | Cost/mo |
|---|---|---|
| Scheduler | AWS EventBridge | ~$0 |
| Compute | AWS Lambda (~150 invocations/mo) | ~$0 |
| Database (permanent, all data) | Supabase Free | Free |
| Raw JSON storage | AWS S3 Intelligent-Tiering | ~$0.02 |
| Email delivery | AWS SES | ~$0 |
| API credentials | AWS Secrets Manager (~4 secrets) | ~$0.40 |
| Rule thresholds | AWS SSM Standard Parameters | Free |
| Guidelines + schema files | AWS S3 | ~$0 |
| LLM — daily briefing (Haiku) | Anthropic API | ~$0.50 |
| LLM — weekly anomaly scan (Sonnet) | Anthropic API | ~$0.50 |
| LLM — ad-hoc queries | Anthropic API | ~$1-3 |
| **Total** | | **~$2-4/mo** |

---

## Scaling Up

- **Bigger database**: Supabase Pro ($25/mo, 8 GB, daily backups, no pausing)
- **More users**: schema and RLS are already multi-user ready; add user onboarding flow
- **iOS push notifications**: move from SES to APNs via native iOS app (Phase 7)
- **Statistical sophistication**: add changepoint detection, lagged correlation analysis on `daily_features` as data accumulates

---

## ~~Archived Options~~

*Not pursuing for now. Revisit if the serverless stack has pain points.*

---

### ~~Mac Mini (Local Self-Hosted)~~

M4 Mac Mini (~$600 new) running the pipeline as a local daemon. Best Apple Health privacy (data never leaves device). Long-term cost advantage after ~month 25.

**Why archived:** Serverless EventBridge + Lambda is simpler, cheaper to start, zero hardware to maintain. Revisit if Apple Health data privacy becomes a concern or if monthly cloud costs grow unexpectedly.

---

### ~~OpenClaw (AI Agent Runtime)~~

Open-source self-hosted AI agent framework with a skill ecosystem for health data (Hevy, Apple Health, Strava). 250k+ GitHub stars. AWS Lightsail one-click deploy as of March 2026.

**Why archived:** This project doesn't need a persistent agent runtime — it's a daily ETL pipeline, a rules engine, and one LLM call. OpenClaw would add a fast-moving open-source dependency, a complex security surface, and an always-on server. The custom stack gives full control at lower cost. Revisit as a potential frontend layer if the skill ecosystem matures significantly.

---

*Document updated June 2026.*

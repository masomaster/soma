# Apple Health export → Soma (Phase 7)

**Goal:** Land Apple Health (and HealthKit–proxied) data in **`biometrics`** and per-workout **`cardio_events`** using **raw JSON in S3 first**, then normalize → Postgres.

---

## How it works (very simple)

1. **On your iPhone**, the Health app collects data from Apple Watch, Strava, Nike Run Club, etc. (whatever you’ve allowed to write into **Apple Health / HealthKit**).

2. **Health Auto Export** (third-party iOS app) reads HealthKit and can send a **JSON POST** to a URL you configure — **when you use a tier that supports automated / REST API exports**. The **free tier often does not** include hands-off scheduling; that’s commonly a **paid upgrade** (verify current pricing in the App Store / the developer’s site — amounts change). If you only have manual export, you can still **verify Soma** by saving JSON and running the smoke script or `curl` against your ingest URL (see below).

3. **AWS** runs a small **Lambda** for each POST: saves the **raw JSON** to **S3**, parses the JSON, writes **`biometrics`** (daily metrics like steps, sleep, HRV) and **`cardio_events`** (each workout/run/ride when HAE includes `data.workouts`).

4. Your **daily briefing pipeline** (later that day) already reads `biometrics` / `cardio_events` from Postgres — so new data shows up on the next rollup without you doing anything else.

---

## Why Apple first while Strava is paused

**Strava is paused** (no live API / OAuth track), and **Nike Run Club** is not a maintained ongoing API. On iPhone, **Strava and NRC often sync activities into Apple Health** anyway. So the **interim path** is: data lands in HealthKit → **Health Auto Export** POSTs to Soma.

**Repo behavior:** HAE **`data.metrics`** → **`biometrics`**; HAE **`data.workouts`** → **`cardio_events`** (`source = apple_health`). Extend `pipeline/adapters/apple_health_export.py` (metric name map) and `pipeline/adapters/apple_health_workouts.py` (workout fields) as you see real payloads.

**HRV note:** HAE often exposes **SDNN** as `heart_rate_variability_sdnn`. Soma stores it under **`hrv_rmssd`** as a **v0 proxy** — not identical to RMSSD.

---

## Health Auto Export: paid automation vs verifying for free

**What went wrong in messaging:** Phase 1 in the implementation plan meant “don’t freeze **Postgres migrations** from guesses before you’ve seen **real vendor JSON**” — not “don’t spend $25” or “don’t validate Apple paths early.” Sorry that wasn’t clearer, and sorry we didn’t call out **HAE’s paywall for automation** up front.

**Reality:** Many users only get **manual** export on the free tier; **scheduled / webhook / REST automation** is often **paid**. That’s between you and the app vendor — Soma only needs a **POST body** in the documented JSON shape when you’re ready.

**Ways to verify Soma without paying for HAE automation yet:**

| Approach | What it proves |
|----------|----------------|
| **Manual JSON file** | If HAE (or another tool) lets you export JSON once for free, save the file and run `python scripts/smoke_apple_health.py normalize <file.json>` then `db-upsert` with `SOMA_DATABASE_URL` — same normalizers the Lambda uses. |
| **`curl` / Bruno** | POST a redacted fixture or a one-time export to your **`AppleHealthIngestUrl`** with headers `X-Soma-User-Id` + optional `X-Soma-Webhook-Secret`. |
| **iOS Shortcuts** (free) | Build a shortcut that reads Health (where Shortcuts supports it) and `POST`s JSON to your URL — more DIY, no HAE subscription. |
| **One-time paid month** | If the only blocker is automation, a single month can be enough to capture real payloads and tune `apple_health_export` / `apple_health_workouts` mappings. |

You do **not** need paid HAE to validate that **Hevy**, **DB**, **briefing**, etc. work — only the **automated phone → URL** path for Apple is vendor-priced.

---

## Supported JSON shapes

1. **Soma daily envelope** (tests / manual tools):

   ```json
   {
     "source": "apple_health_export",
     "event_date": "2024-06-01",
     "metrics": [
       { "metric": "hrv_rmssd", "value": 48.2, "unit": "ms" }
     ]
   }
   ```

2. **Health Auto Export** — `{"data":{"metrics":[...],"workouts":[...]}}` per the [API JSON wiki](https://github.com/Lybron/health-auto-export/wiki/API-Export---JSON-Format). The Lambda accepts **either half**: `{"data":{"metrics":[...]}}` alone still upserts **biometrics**; `{"data":{"workouts":[...]}}` alone still upserts **cardio_events** (and writes raw to S3). Empty or missing arrays are fine.

### Two automations (metrics vs workouts)

Use the **same** **`AppleHealthIngestUrl`**, **POST**, and **headers** (`X-Soma-User-Id`, optional `X-Soma-Webhook-Secret`) for both:

| Automation | HAE focus | Body shape (conceptually) |
|------------|-----------|---------------------------|
| **Metrics** | Quantities / recovery series | `{"data":{"metrics":[...]}}` |
| **Workouts** | Sessions | `{"data":{"workouts":[...]}}` |

**Biometrics** rows merge on `(user_id, source, event_date, metric)` with **`DO UPDATE`**, so overlapping metric windows from two schedules stay idempotent. **Cardio** uses **`ON CONFLICT (user_id, source_id) DO NOTHING`**: re-posting the same Apple Health workout UUID does not duplicate rows.

### Hevy vs Apple Health “duplicate” strength sessions

**Hevy** ETL writes **`strength_events`** (per set) with `source = hevy`. **Apple Health** workouts from HAE write **`cardio_events`** with `source = apple_health`. The webhook **drops** Apple strength-typed cardio rows when there is **any** Hevy `strength_events` row on the **same calendar day** (activity types: *Traditional Strength Training*, *Functional Strength Training*, *Core Training* — extend in `pipeline/apple_hevy_cardio_dedup.py` if needed).

The JSON response includes **`cardio_events_dropped_hevy_strength_dup`** (count skipped before upsert).

### Health Auto Export: “Key” vs “Value” (headers)

In **Add Headers**, **Key** is the **HTTP header name** (left column), **Value** is the header body (right column). Examples:

| Key | Value |
|-----|-------|
| `X-Soma-User-Id` | Your Supabase Auth user UUID |
| `X-Soma-Webhook-Secret` | Same random string as **`APPLE_HEALTH_WEBHOOK_SECRET`** in Secrets Manager (or Lambda env override) |

**`APPLE_HEALTH_WEBHOOK_SECRET`** can be **any long random string** you invent (password manager, `openssl rand -hex 32`, etc.). Prefer storing it in **`soma-{env}-lambda-runtime`** JSON (key `APPLE_HEALTH_WEBHOOK_SECRET`) so it is not visible in the Lambda console; optional **Lambda environment variable** with the same name **overrides** the JSON value for emergencies. The placeholder **`update_me`** in JSON or env is treated as **unset** (no header required). If no real secret is configured, do **not** send `X-Soma-Webhook-Secret`.

---

## AWS (after `cdk deploy`)

1. **CloudFormation → Outputs** — copy **`AppleHealthIngestUrl`** (ends with `/ingest/apple-health`). That is the **only URL** you paste into Health Auto Export.

2. **Secrets Manager** — same secret as the briefing Lambda: `soma-{staging|prod}-lambda-runtime`. It must contain a valid **`DB_CONNECT_STRING`**. Optionally add **`APPLE_HEALTH_WEBHOOK_SECRET`** (long random string). **`update_me`** for that key is treated as **disabled** until you replace it. Fresh CDK seeds include this key with `update_me` so the shape is documented.

3. **Lambda** `soma-{env}-apple-health-webhook` → **Configuration → Environment variables** (optional): **`APPLE_HEALTH_WEBHOOK_SECRET`** overrides the JSON key when set to a non-placeholder value. Usually leave unset.

4. **Health Auto Export** (iOS) — one or two **REST API** / webhook automations (same URL and headers):  
   - **URL:** the output URL (HTTPS **POST**).  
   - **Headers:** **`X-Soma-User-Id: <your Supabase Auth user UUID>`** (same tenant as `user_settings` / briefing).  
   - Optional: **`X-Soma-Webhook-Secret`** when a real webhook secret is configured (step 2 or 3).  
   - Either one automation with **metrics + workouts**, or **split** into metrics-only and workouts-only (see **Two automations** above).

5. **S3** — raw files land in the stack’s ingest bucket under  
   `raw/{user_id}/apple_health_export/{YYYY-MM-DD}/{timestamp}.json`.

See **`infrastructure/lambda/apple_health_webhook/README.md`** for a short operator checklist.

### Troubleshooting: no Lambda logs

1. **API Gateway access logs** — CloudWatch → Log groups → **`/aws/apigateway/soma-{env}-apple-health-access`** (stack output **`AppleHealthHttpApiAccessLogGroup`**). If this log group has **no** log streams after an HAE run, the client never reached your execute-api URL (wrong URL, typo, phone offline, or automation not firing). If you see lines with **`404`** or **`403`**, the route or auth does not match (e.g. wrong path — must be **`POST`** `…/ingest/apple-health`). **`502`/`504`** often means Lambda threw or timed out (check the function’s log group).
2. **Lambda** — `/aws/lambda/soma-{env}-apple-health-webhook` only receives traffic after API Gateway invokes the integration (typically **2xx** from API GW’s perspective unless the Lambda returns an error mapping). On **400**, the response JSON includes **`error`** (machine code) and **`hint`** (what to fix). Typical causes: missing **`X-Soma-User-Id`** header (HAE “Key” must be exactly `X-Soma-User-Id`), **empty POST body** (automation must send JSON, not headers-only), or **invalid JSON** (export format must be JSON, not CSV).

---

## Code (in repo)

| Piece | Role |
|-------|------|
| `pipeline/adapters/apple_health_export.py` | HAE metrics + envelope → `biometrics`; `ingest_apple_health_payload_complete` → raw + biometrics + cardio rows. |
| `pipeline/adapters/apple_health_workouts.py` | HAE `data.workouts` (v2 + v1-style) → `cardio_events`. |
| `pipeline/biometrics_upsert.py` | `ON CONFLICT DO UPDATE` on `biometrics`. |
| `pipeline/apple_hevy_cardio_dedup.py` | Before cardio upsert: skip Apple strength ``cardio_events`` on days Hevy already has sets. |
| `pipeline/lambda_secrets.py` | `resolve_db_connect_string()`; optional `APPLE_HEALTH_WEBHOOK_SECRET` from same JSON or env. |
| `infrastructure/soma_cdk/apple_health_ingest.py` | S3 bucket + HTTP API + webhook Lambda (shares pipeline layer with briefing). |
| `pipeline/apple_health_webhook_event.py` | API Gateway event parsing (headers, body, JSON) for the Apple Health webhook. |
| `infrastructure/lambda/apple_health_webhook/handler.py` | POST handler. |

---

## Local smoke (`scripts/smoke_apple_health.py`)

Same env patterns as Hevy smoke: **`SOMA_USER_ID`**, **`SOMA_DATABASE_URL`** for `db-upsert`, optional **`SOMA_RAW_LOCAL_DIR`**.

```bash
python scripts/smoke_apple_health.py normalize
python scripts/smoke_apple_health.py normalize tests/fixtures/biometrics/health_auto_export_workouts_redacted.json

python scripts/smoke_apple_health.py raw-disk
python scripts/smoke_apple_health.py db-upsert
```

Apply **`schema/migrations/0001_initial.sql`** (+ later migrations) before `db-upsert`.

---

## Sending a real capture for tuning

If a field is missing (pace, elevation, HR), paste a **redacted** JSON snippet (or describe `data.workouts[]` shape) and we can extend `apple_health_workouts.py` — HAE varies by activity type and iOS version.

See also [integrations-checklist.md](./integrations-checklist.md) and [local-dev-and-tooling.md](./local-dev-and-tooling.md).

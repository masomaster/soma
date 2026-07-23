# Cycling power ingest + FTP estimation

**Goal:** Land **power-meter watts** from a Wahoo ELEMNT BOLT (and a one-time Strava archive for legacy rides) into Soma, then estimate **FTP** from mean-maximal power — without a 20-minute ramp test and without a paid Strava API subscription.

Apple Health / Health Auto Export **does not** carry usable power into Soma. The live Strava REST API remains **paused** (Standard Tier needs a Strava subscription). This path uses **FIT files** instead.

## Ingest modes (do not conflate)

| Mode | Source flag | Cadence | Role |
|------|-------------|---------|------|
| **Dropbox FIT** | `wahoo_fit` | **Recurring / scheduled** (new BOLT rides keep landing in Dropbox) | Canonical ongoing power path |
| **Strava bulk export** | `strava_export` | **One-time** historical backfill | Legacy rides already on Strava before Dropbox ingest existed — **not** a recurring sync |

After the Strava archive is ingested once, do **not** re-export on a schedule. Ongoing watts come only from Dropbox → `wahoo_fit`. Cross-source dedup already prefers `wahoo_fit` over `strava_export` if both exist for the same session.

---

## Data flow

1. **Ongoing (scheduled in AWS):** BOLT → ELEMNT companion → **Dropbox** auto-export of `.fit` → **Dropbox API** Lambda (`soma-wahoo-fit-ingest`, daily **08:30 UTC**) → raw S3 + `cardio_events` + FTP estimate. **No Mac required.**
2. **One-time backfill:** Strava website → **Request Your Archive** (free) → unzip → **single** `python -m pipeline.fit_ingest --source strava_export` run.
3. Adapter writes a **JSON raw envelope** (base64 payload + sha256) to S3 under the usual `raw/{user_id}/{source}/…/.json` key, then normalizes to `cardio_events` (including `avg_watts`, `power_mmp_json`, …).
4. The scheduled job always runs FTP estimation after upsert (90-day best MMP → prefers maximal 60/30-min anchors, else soft-hour-aware Coggan / scaled critical power → `daily_health_metrics.ftp_*`). Optional local `--estimate-ftp` does the same.
Sources: `wahoo_fit` (Dropbox API, recurring), `strava_export` (archive, one-shot). Dedup priority: **wahoo_fit > strava_export > apple_health**.

---

## Operator setup

### BOLT → Dropbox API (recurring — preferred)

1. In the Wahoo ELEMNT app, enable **Dropbox** as an upload / auto-export target. Confirm new rides appear as `.fit` files in Dropbox (cloud), e.g. `/Apps/WahooFitness`.
2. Create a Dropbox app at [dropbox.com/developers/apps](https://www.dropbox.com/developers/apps):
   - Prefer **Full Dropbox** so the Wahoo folder is visible; or **App folder** if FITs live in that app’s root.
   - Generate an **offline** refresh token (one-shot helper):

```bash
python3.14 scripts/dropbox_oauth_refresh_token.py
```

3. After `cdk deploy`, fill Secrets Manager **`soma-dropbox`** with the printed JSON:

```json
{
  "DROPBOX_APP_KEY": "…",
  "DROPBOX_APP_SECRET": "…",
  "DROPBOX_REFRESH_TOKEN": "…",
  "DROPBOX_FOLDER": "/Apps/WahooFitness"
}
```

Use `""` for `DROPBOX_FOLDER` when the app has App-folder access and FITs are at that root. Redeploy once with **`SeedRuntimeSecrets=No`** so CloudFormation does not overwrite the secret.

4. Confirm schedule: EventBridge Scheduler `soma-wahoo-fit-ingest` at **08:30 UTC**. The job only downloads activity files modified in the last **45 days** (`SOMA_DROPBOX_LOOKBACK_DAYS`). Manual invoke:

```bash
aws lambda invoke --function-name soma-wahoo-fit-ingest /tmp/wahoo-out.json && cat /tmp/wahoo-out.json
```

Local smoke (same credentials via env, no Lambda):

```bash
export DROPBOX_APP_KEY=… DROPBOX_APP_SECRET=… DROPBOX_REFRESH_TOKEN=… DROPBOX_FOLDER=/Apps/WahooFitness
# plus SOMA_USER_ID / DB — or run the Lambda path via tests’ mocks
```

### Optional: local Mac folder + launchd (fallback only)

Only if you need a laptop-side path (Mac awake + Dropbox desktop sync). Prefer the API Lambda above.

```bash
SOMA_WAHOO_FIT_DIR=~/Dropbox/Apps/WahooFitness
make wahoo-fit-ingest                 # once
make wahoo-fit-ingest-install         # launchd — requires open/awake Mac
make wahoo-fit-ingest-uninstall
```

### Strava archive (one-time legacy backfill)

Run **once** to pull historical power that predates Dropbox ingest. Do not schedule this.

1. On **strava.com** → Settings → **My Account** → Download your account → **Request Your Archive** (email link; can take hours).
2. Unzip the archive. Activity files live under `activities/` as `.fit` / `.fit.gz` / `.tcx` / `.gpx` (format depends on original upload). BOLT→Strava rides are usually FIT and retain power.
3. Ingest once:

```bash
python -m pipeline.fit_ingest \
  --user-id "$SOMA_USER_ID" \
  --source strava_export \
  --dir /path/to/export_XXXX \
  --estimate-ftp
```

Titles from `activities.csv` are attached as `notes` when present. Files without a power stream still create cardio rows tagged `no_power` in `quality_flags`.

Dry-run local FIT folder:

```bash
pip install -e '.[fit]'
python -m pipeline.fit_ingest --user-id "$SOMA_USER_ID" --source wahoo_fit --dir ./fits --dry-run -v
```

---

## Schema

Migration [`0011_cardio_power_and_ftp.sql`](../../schema/migrations/0011_cardio_power_and_ftp.sql):

| Table | Columns |
|-------|---------|
| `cardio_events` | `avg_watts`, `max_watts`, `normalized_power`, `work_kj`, `device_watts`, `power_mmp_json` |
| `daily_health_metrics` | `ftp_watts`, `ftp_method`, `ftp_confidence` |

`power_mmp_json` maps duration seconds → watts (e.g. `"1200"` → best 20-minute mean).

---

## FTP method (honest limits)

Deterministic math in [`pipeline/power_math.py`](../../pipeline/power_math.py) — **not** the LLM.
Estimates prefer **longer sustained efforts** so short anaerobic peaks cannot inflate FTP:

1. **Best 60-min MMP** when the hour looks maximal vs 30-min → `ftp ≈ MMP_60`.
2. Else **best 30-min** → `ftp = 0.95 × MMP_30` (also prevents short-peak models from winning when 30-min exists).
3. Else **critical power** (2-parameter fit on 5–30 min MMP) → `ftp = 0.95 × CP`.
4. Else **Coggan 20-min** when the effort gate passes → `ftp = 0.90 × MMP_20` (outdoor-conservative; classic lab protocol uses 0.95 on a paced test).
5. Else `insufficient_data`.

A soft hour on an FTP-test day (hard 20-min + easy warmup/cooldown) does **not** force a low `mmp_60` over a solid 30-min estimate.

MMP curves are **monotone-clamped** (longer windows cannot exceed shorter) before fitting, because pointwise-max across rides can create non-physiological envelopes.

**Caveats:** Outdoor best efforts include drafting, surges, and non-maximal “hard” days. Treat `ftp_watts` as an **estimate**; use `ftp_confidence` and re-run after more hard rides. Session RPE is optional later for labeling intentional efforts — not required for v1.

---

## Dashboard

**Training** tab → **Cycling power**: estimated FTP (method + confidence), last-ride avg/NP, FTP-by-day chart, and a short list of recent power rides. Data from `daily_health_metrics.ftp_*` and `cardio_events` watts columns.

---

## Code map

| Module | Role |
|--------|------|
| [`pipeline/adapters/fit_activity.py`](../../pipeline/adapters/fit_activity.py) | FIT/TCX/GPX parse + normalize |
| [`pipeline/dropbox_api.py`](../../pipeline/dropbox_api.py) | Dropbox OAuth refresh + list/download |
| [`pipeline/wahoo_fit_scheduled_ingest.py`](../../pipeline/wahoo_fit_scheduled_ingest.py) | Dropbox → upsert + FTP (Lambda job) |
| [`pipeline/fit_ingest.py`](../../pipeline/fit_ingest.py) | Directory CLI (local / Strava archive) |
| [`pipeline/power_math.py`](../../pipeline/power_math.py) | MMP / NP / FTP |
| [`pipeline/ftp_estimate.py`](../../pipeline/ftp_estimate.py) | Load rides + persist `ftp_*` |
| [`pipeline/power_cardio_dedup.py`](../../pipeline/power_cardio_dedup.py) | Cross-source near-dup |
| [`infrastructure/lambda/wahoo_fit_ingest/`](../../infrastructure/lambda/wahoo_fit_ingest/) | Scheduled Lambda handler |

Dependency: **`fitdecode`** in the Lambda layer and optional local extra **`.[fit]`**.

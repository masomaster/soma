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

1. **Ongoing (scheduled):** BOLT → ELEMNT companion → **Dropbox** auto-export of `.fit` → sync folder → recurring `python -m pipeline.fit_ingest --source wahoo_fit` (cron / EventBridge / local schedule — ops choice).
2. **One-time backfill:** Strava website → **Request Your Archive** (free) → unzip → **single** `python -m pipeline.fit_ingest --source strava_export` run.
3. Adapter writes a **JSON raw envelope** (base64 payload + sha256) to S3 under the usual `raw/{user_id}/{source}/…/.json` key, then normalizes to `cardio_events` (including `avg_watts`, `power_mmp_json`, …).
4. Optional `--estimate-ftp` aggregates 90-day best MMP → Coggan 20-min or critical-power estimate → `daily_health_metrics.ftp_*`.

Sources: `wahoo_fit` (Dropbox, recurring), `strava_export` (archive, one-shot). Dedup priority: **wahoo_fit > strava_export > apple_health**.

---

## Operator setup

### BOLT → Dropbox (recurring)

1. In the Wahoo ELEMNT app, enable **Dropbox** as an upload / auto-export target (exact menu labels vary by app version).
2. Confirm new rides appear as `.fit` files in your Dropbox sync folder (path differs; common patterns look like `Dropbox/Apps/…` or a Wahoo-named folder).
3. Schedule a recurring ingest against that folder (idempotent via `source_id` + optional sha256 skip):

```bash
pip install -e '.[fit]'   # fitdecode
python -m pipeline.fit_ingest \
  --user-id "$SOMA_USER_ID" \
  --source wahoo_fit \
  --dir ~/Dropbox/path/to/wahoo/fits \
  --estimate-ftp
```

Set `DATABASE_URL` (or `SOMA_DATABASE_URL`) to persist. Optionally set `SOMA_RAW_BUCKET` for S3 raw envelopes; without it, envelopes are skipped/logged only.

Dry-run (parse only):

```bash
python -m pipeline.fit_ingest --user-id "$SOMA_USER_ID" --source wahoo_fit --dir ./fits --dry-run -v
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

Deterministic math in [`pipeline/power_math.py`](../../pipeline/power_math.py) — **not** the LLM:

1. **Coggan 20-min** when best 20-min MMP exists and is ≥ ~85% of best 5-min MMP → `ftp = 0.95 × MMP_20`.
2. Else **critical power** (2-parameter fit on mid-duration MMP points) when ≥3 points exist → `ftp ≈ CP`.
3. Else `insufficient_data`.

**Caveats:** Outdoor best efforts include drafting, surges, and non-maximal “hard” days. Treat `ftp_watts` as an **estimate**; use `ftp_confidence` and re-run after more hard rides. Session RPE is optional later for labeling intentional efforts — not required for v1.

---

## Code map

| Module | Role |
|--------|------|
| [`pipeline/adapters/fit_activity.py`](../../pipeline/adapters/fit_activity.py) | FIT/TCX/GPX parse + normalize |
| [`pipeline/fit_ingest.py`](../../pipeline/fit_ingest.py) | Directory CLI |
| [`pipeline/power_math.py`](../../pipeline/power_math.py) | MMP / NP / FTP |
| [`pipeline/ftp_estimate.py`](../../pipeline/ftp_estimate.py) | Load rides + persist `ftp_*` |
| [`pipeline/power_cardio_dedup.py`](../../pipeline/power_cardio_dedup.py) | Cross-source near-dup |

Dependency: optional extra **`.[fit]`** (`fitdecode`). Dev installs include it via `make install`.

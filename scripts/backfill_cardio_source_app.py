#!/usr/bin/env python3
"""Backfill ``cardio_events.source_app`` / ``started_at`` from raw S3 and clean duplicates.

Migration 0006 added ``source_app`` and ``started_at`` to ``cardio_events``. Rows
written before it are NULL, so the source-aware hub dedup
(:func:`pipeline.apple_health_cardio_dedup.filter_near_duplicate_apple_cardio`) can
neither attribute nor prioritize them. This script:

1. Re-reads raw Health Auto Export payloads (S3 ``raw/{user}/apple_health_export/...``
   or a local dir) and re-normalizes workouts to recover ``source_app`` + ``started_at``.
2. **Enriches** existing ``apple_health`` cardio rows (matched by ``source_id``) with
   those two columns.
3. Optionally **re-inserts** raw workouts missing from the DB (``--reinsert-missing``),
   so an accurate row a prior dedup wrongly dropped can come back.
4. **Cleans** duplicate clusters: keeps the highest-priority source per cluster
   (NRC > Strava > Apple Watch/iPhone > Fitbit/Google) and deletes the losers.

Dry-run by default — prints the plan. Pass ``--apply`` to write changes.

Env (same as scripts/smoke_apple_health.py): ``SOMA_USER_ID``, ``SOMA_DATABASE_URL``
(or ``DATABASE_URL``). Raw source: ``--bucket``/``SOMA_RAW_BUCKET``/``RAW_BUCKET``
for S3, or ``--local-raw-dir`` (defaults to ``SOMA_RAW_LOCAL_DIR``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_REPO_ROOT / ".env")


def _die(msg: str, code: int = 1) -> NoReturn:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        _die(f"Missing environment variable {name}. See scripts/smoke_apple_health.py docs.")
    return v


def _iter_raw_payloads_s3(bucket: str, user_id: str) -> list[dict[str, Any]]:
    import boto3

    from pipeline.adapters.apple_health_export import APPLE_HEALTH_EXPORT_SOURCE
    from pipeline.raw_storage import raw_prefix

    s3 = boto3.client("s3")
    prefix = raw_prefix(user_id, APPLE_HEALTH_EXPORT_SOURCE)
    payloads: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                payloads.append(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"  WARN: skipping unparseable raw object {key}", file=sys.stderr)
    return payloads


def _iter_raw_payloads_local(local_dir: Path, user_id: str) -> list[dict[str, Any]]:
    from pipeline.adapters.apple_health_export import APPLE_HEALTH_EXPORT_SOURCE

    root = local_dir / "raw" / user_id / APPLE_HEALTH_EXPORT_SOURCE
    if not root.is_dir():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"  WARN: skipping unparseable raw file {path}", file=sys.stderr)
    return payloads


def _normalize_raw_rows(payloads: list[dict[str, Any]], user_id: str) -> dict[str, dict[str, Any]]:
    """Return ``{source_id: normalized_row}`` merged across all raw payloads."""
    from pipeline.adapters import apple_health_workouts

    by_source_id: dict[str, dict[str, Any]] = {}
    for body in payloads:
        for row in apple_health_workouts.normalize_apple_health_cardio_from_payload(body, user_id):
            sid = row.get("source_id")
            if isinstance(sid, str) and sid:
                by_source_id[sid] = row
    return by_source_id


def _load_db_rows(cur: Any, user_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, source, source_app, source_id, event_date, started_at,
               activity_type, duration_min, distance_miles, elevation_ft,
               avg_hr, max_hr, avg_pace_sec_mi, calories, effort_zone, session_rpe, notes
        FROM cardio_events
        WHERE user_id = %s::uuid AND source = 'apple_health'
        ORDER BY event_date
        """,
        (user_id,),
    )
    cols = (
        "id", "source", "source_app", "source_id", "event_date", "started_at",
        "activity_type", "duration_min", "distance_miles", "elevation_ft",
        "avg_hr", "max_hr", "avg_pace_sec_mi", "calories", "effort_zone",
        "session_rpe", "notes",
    )
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def run_backfill(*, apply: bool, reinsert_missing: bool, raw_source: str, local_dir: Path | None) -> None:
    import psycopg2

    from pipeline.apple_health_cardio_dedup import apple_cardio_rows_to_drop
    from pipeline.cardio_upsert import delete_cardio_events_by_source_id, upsert_cardio_events

    user_id = _require_env("SOMA_USER_ID")
    dsn = (
        os.environ.get("SOMA_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not dsn:
        _die("Set SOMA_DATABASE_URL (or DATABASE_URL) to your Supabase Postgres URI.")

    if local_dir is not None:
        payloads = _iter_raw_payloads_local(local_dir, user_id)
        print(f"Loaded {len(payloads)} raw payload(s) from {local_dir}")
    else:
        payloads = _iter_raw_payloads_s3(raw_source, user_id)
        print(f"Loaded {len(payloads)} raw payload(s) from s3://{raw_source}")

    raw_by_sid = _normalize_raw_rows(payloads, user_id)
    print(f"Normalized {len(raw_by_sid)} distinct workout(s) from raw.")

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                db_rows = _load_db_rows(cur, user_id)
                print(f"Found {len(db_rows)} existing apple_health cardio row(s) in DB.\n")

                db_by_sid = {r["source_id"]: r for r in db_rows}

                # --- Phase A: enrich existing rows with source_app / started_at ---
                enrich: list[tuple[str, str | None, Any]] = []
                for sid, raw_row in raw_by_sid.items():
                    db_row = db_by_sid.get(sid)
                    if db_row is None:
                        continue
                    need_app = db_row.get("source_app") is None and raw_row.get("source_app")
                    need_start = db_row.get("started_at") is None and raw_row.get("started_at")
                    if need_app or need_start:
                        enrich.append((sid, raw_row.get("source_app"), raw_row.get("started_at")))
                        # reflect locally so dedup sees enriched values
                        db_row["source_app"] = raw_row.get("source_app")
                        db_row["started_at"] = raw_row.get("started_at")
                print(f"Phase A — enrich source_app/started_at: {len(enrich)} row(s)")
                for sid, app, start in enrich[:20]:
                    print(f"    {sid}  source_app={app!r} started_at={start}")
                if len(enrich) > 20:
                    print(f"    … {len(enrich) - 20} more")

                # --- Phase B: optionally re-insert raw workouts missing from DB ---
                missing = [raw_by_sid[s] for s in raw_by_sid if s not in db_by_sid]
                print(f"\nPhase B — raw workouts missing from DB: {len(missing)}"
                      f" ({'will re-insert' if reinsert_missing else 'skipped; use --reinsert-missing'})")
                for row in missing[:20]:
                    print(f"    {row['event_date']} {row['activity_type']} "
                          f"source_app={row.get('source_app')!r} sid={row['source_id']}")
                if len(missing) > 20:
                    print(f"    … {len(missing) - 20} more")

                # Set of rows to dedup: enriched DB rows (+ missing if re-inserting).
                dedup_set = list(db_rows)
                if reinsert_missing:
                    dedup_set = dedup_set + missing

                # --- Phase C: compute duplicate losers to delete ---
                losers = apple_cardio_rows_to_drop(dedup_set)
                loser_identity = {id(r) for r in losers}
                # Only delete rows that actually exist in the DB.
                loser_sids = [
                    r["source_id"] for r in losers
                    if isinstance(r.get("source_id"), str) and r["source_id"] in db_by_sid
                ]
                # Never re-insert a row that dedup would immediately drop — only winners.
                missing_to_insert = [r for r in missing if id(r) not in loser_identity]
                print(f"\nPhase C — duplicate rows to delete: {len(loser_sids)}")
                for r in losers[:40]:
                    in_db = "DB" if r.get("source_id") in db_by_sid else "raw-only (skipped)"
                    print(f"    DROP {r['event_date']} {r['activity_type']} "
                          f"source_app={r.get('source_app')!r} dur={r.get('duration_min')} [{in_db}] "
                          f"sid={r.get('source_id')}")
                if reinsert_missing:
                    print(f"    (re-inserting {len(missing_to_insert)} winner(s); "
                          f"skipping {len(missing) - len(missing_to_insert)} missing loser(s))")

                if not apply:
                    print("\nDRY RUN — no changes written. Re-run with --apply to persist.")
                    conn.rollback()
                    return

                # Apply: enrich, reinsert winners, delete losers.
                import urllib.parse

                host = urllib.parse.urlsplit(dsn).hostname or "?"
                print(f"\nAPPLYING to db host={host} user_id={user_id}")
                for sid, app, start in enrich:
                    cur.execute(
                        "UPDATE cardio_events SET source_app = %s, started_at = %s "
                        "WHERE user_id = %s::uuid AND source_id = %s",
                        (app, start, user_id, sid),
                    )
                if reinsert_missing and missing_to_insert:
                    upsert_cardio_events(cur, missing_to_insert)
                deleted = delete_cardio_events_by_source_id(
                    cur, user_id=user_id, source_ids=loser_sids
                )
                print(f"\nAPPLIED — enriched={len(enrich)} "
                      f"reinserted={len(missing_to_insert) if reinsert_missing else 0} deleted={deleted}")
    finally:
        conn.close()


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist changes (default: dry run)")
    parser.add_argument(
        "--reinsert-missing",
        action="store_true",
        help="Re-insert raw workouts absent from the DB before dedup (recovers dropped rows)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SOMA_RAW_BUCKET", "").strip() or os.environ.get("RAW_BUCKET", "").strip(),
        help="S3 raw bucket (or set SOMA_RAW_BUCKET / RAW_BUCKET)",
    )
    parser.add_argument(
        "--local-raw-dir",
        type=Path,
        default=None,
        help="Read raw JSON from a local dir instead of S3 (defaults to SOMA_RAW_LOCAL_DIR)",
    )
    args = parser.parse_args()

    local_dir = args.local_raw_dir
    if local_dir is None:
        env_local = os.environ.get("SOMA_RAW_LOCAL_DIR", "").strip()
        # Only use env local dir when no bucket is configured.
        if env_local and not args.bucket:
            local_dir = Path(env_local)

    if local_dir is None and not args.bucket:
        _die("Provide --bucket (or SOMA_RAW_BUCKET/RAW_BUCKET) or --local-raw-dir.")

    run_backfill(
        apply=args.apply,
        reinsert_missing=args.reinsert_missing,
        raw_source=args.bucket,
        local_dir=local_dir.resolve() if local_dir else None,
    )


if __name__ == "__main__":
    main()

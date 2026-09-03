#!/usr/bin/env python3
"""Re-ingest sleep from raw HAE payloads and re-roll daily metrics/features.

Use after sleep upsert last-write-wins corrupted full nights with morning
fragments. Re-reads ``raw/{user}/apple_health_export/**/*.json``, normalizes
with the fragment filter + GREATEST sleep upsert, then rolls up
``daily_health_metrics`` and recomputes ``daily_features`` for affected days.

  python scripts/reprocess_apple_health_sleep.py --dry-run
  python scripts/reprocess_apple_health_sleep.py --apply

Env: ``SOMA_USER_ID``, ``SOMA_DATABASE_URL`` (or ``DATABASE_URL``), and either
``SOMA_RAW_BUCKET`` / ``RAW_BUCKET`` / ``--bucket`` or ``--local-raw-dir``.
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
        _die(f"Missing environment variable {name}.")
    return v


def _iter_raw_objects_s3(bucket: str, user_id: str) -> list[tuple[str, dict[str, Any]]]:
    import boto3

    from pipeline.adapters.apple_health_export import APPLE_HEALTH_EXPORT_SOURCE
    from pipeline.raw_storage import raw_prefix

    s3 = boto3.client("s3")
    prefix = raw_prefix(user_id, APPLE_HEALTH_EXPORT_SOURCE)
    out: list[tuple[str, dict[str, Any]]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                out.append((key, json.loads(body.decode("utf-8"))))
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"  WARN: skip unparseable {key}", file=sys.stderr)
    out.sort(key=lambda x: x[0])
    return out


def _iter_raw_objects_local(
    local_dir: Path, user_id: str
) -> list[tuple[str, dict[str, Any]]]:
    from pipeline.adapters.apple_health_export import APPLE_HEALTH_EXPORT_SOURCE

    root = local_dir / "raw" / user_id / APPLE_HEALTH_EXPORT_SOURCE
    if not root.is_dir():
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            out.append((str(path), json.loads(path.read_text(encoding="utf-8"))))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"  WARN: skip unparseable {path}", file=sys.stderr)
    return out


def _payload_has_sleep(body: dict[str, Any]) -> bool:
    data = body.get("data")
    if not isinstance(data, dict):
        return False
    metrics = data.get("metrics")
    if not isinstance(metrics, list):
        return False
    for block in metrics:
        if not isinstance(block, dict):
            continue
        name = str(block.get("name") or "").lower().replace(" ", "_")
        if "sleep" in name:
            return True
    return False


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default=None, help="S3 raw bucket")
    parser.add_argument(
        "--local-raw-dir",
        type=Path,
        default=None,
        help="Local dir containing raw/{user}/apple_health_export/",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write biometrics / daily_health_metrics / daily_features",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only (default if --apply omitted)",
    )
    args = parser.parse_args()
    dry_run = not args.apply or args.dry_run

    user_id = _require_env("SOMA_USER_ID")
    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if not dsn:
        _die("Set SOMA_DATABASE_URL or DATABASE_URL.")

    bucket = (
        (args.bucket or "").strip()
        or os.environ.get("SOMA_RAW_BUCKET", "").strip()
        or os.environ.get("RAW_BUCKET", "").strip()
    )
    local_raw = ""
    if args.local_raw_dir is not None:
        local_raw = str(args.local_raw_dir).strip()
    if not local_raw:
        local_raw = os.environ.get("SOMA_RAW_LOCAL_DIR", "").strip()

    if bucket:
        objects = _iter_raw_objects_s3(bucket, user_id)
        print(f"Listed {len(objects)} raw object(s) from s3://{bucket}")
    elif local_raw:
        objects = _iter_raw_objects_local(Path(local_raw), user_id)
        print(f"Listed {len(objects)} raw file(s) under {local_raw}")
    else:
        _die("Pass --bucket / SOMA_RAW_BUCKET or --local-raw-dir / SOMA_RAW_LOCAL_DIR.")

    from pipeline.adapters import apple_health_export
    from pipeline.biometrics_daily_rollup import (
        event_dates_from_biometrics_rows,
        rollup_biometrics_dates,
    )
    from pipeline.biometrics_upsert import upsert_biometrics
    from pipeline.daily_features_recompute import (
        feature_dates_affected_by_metric_days,
        recompute_daily_features,
    )
    import psycopg2

    sleep_objects = [(k, b) for k, b in objects if _payload_has_sleep(b)]
    print(f"Payloads with sleep metrics: {len(sleep_objects)}")

    all_rows: list[dict[str, Any]] = []
    for key, body in sleep_objects:
        rows = apple_health_export.normalize_apple_health_export_payload(
            body, user_id=user_id
        )
        for r in rows:
            r["raw_s3_key"] = key
        sleep_rows = [
            r
            for r in rows
            if r.get("metric") in {"sleep_hours", "sleep_deep_hrs", "sleep_rem_hrs"}
        ]
        if sleep_rows:
            sleep_dates = [
                r["event_date"].isoformat()
                for r in sleep_rows
                if r["metric"] == "sleep_hours"
            ]
            print(f"  {key}: {len(sleep_rows)} sleep row(s) dates={sleep_dates}")
        all_rows.extend(sleep_rows)

    # Collapse same (source, date, metric) across payloads — keep the longer value.
    merged: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    for row in all_rows:
        key = (row["user_id"], row["source"], row["event_date"], row["metric"])
        prev = merged.get(key)
        if prev is None or float(row["value"]) >= float(prev["value"]):
            merged[key] = row
    all_rows = list(merged.values())

    dates = event_dates_from_biometrics_rows(all_rows)
    print(
        f"Unique sleep event dates: {len(dates)} "
        f"({dates[0] if dates else '—'} .. {dates[-1] if dates else '—'})"
    )
    print(f"Total sleep biometrics rows to upsert: {len(all_rows)}")

    if dry_run:
        print("Dry-run only; pass --apply to write.")
        return

    feature_dates = feature_dates_affected_by_metric_days(dates)
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                upsert_biometrics(cur, all_rows)
                rolled = rollup_biometrics_dates(cur, user_id=user_id, dates=dates)
                feats = recompute_daily_features(
                    cur, user_id=user_id, dates=feature_dates
                )
        print(
            f"Upserted sleep rows; rolled {len(rolled)} metric day(s); "
            f"recomputed {len(feats)} feature day(s)."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

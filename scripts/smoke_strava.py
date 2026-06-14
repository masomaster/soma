#!/usr/bin/env python3
"""Local smoke: Strava live fetch, raw JSON to disk, optional Postgres upsert.

OAuth access tokens expire; refresh flow is not in this script — paste a fresh
token from your Strava app / OAuth debugger when testing. See .env.example and
docs/plans/local-dev-and-tooling.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

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
        _die(f"Missing environment variable {name}. See .env.example and local-dev-and-tooling.md.")
    return v


def cmd_live() -> None:
    """Layer 1: fetch one page from Strava, normalize, print summary (no disk, no DB)."""
    from pipeline.adapters import strava

    _require_env("STRAVA_ACCESS_TOKEN")
    user_id = _require_env("SOMA_USER_ID")

    activities = strava.fetch_strava_activities_page(os.environ["STRAVA_ACCESS_TOKEN"], page=1)
    rows = strava.normalize_strava_activities(activities, user_id)
    print("live: OK")
    print(f"  activities in page: {len(activities)}")
    print(f"  normalized cardio_events rows: {len(rows)}")
    if rows:
        print(f"  sample source_id: {rows[0].get('source_id')!r}")
        print(f"  sample event_date: {rows[0].get('event_date')!r}")


def cmd_raw_disk() -> None:
    """Layer 2: fetch + write raw JSON under tmp/ (same key layout as S3)."""
    from pipeline.adapters import strava

    _require_env("STRAVA_ACCESS_TOKEN")
    user_id = _require_env("SOMA_USER_ID")
    raw_root = Path(os.environ.get("SOMA_RAW_LOCAL_DIR", "tmp/soma_raw")).resolve()

    def raw_put(key: str, body: bytes) -> None:
        dest = raw_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    utc = datetime.now(timezone.utc)
    rows = strava.fetch_and_normalize_from_api(
        user_id,
        os.environ["STRAVA_ACCESS_TOKEN"],
        raw_put=raw_put,
        utc_now=utc,
    )
    print("raw-disk: OK")
    print(f"  raw_root: {raw_root}")
    print(f"  normalized rows: {len(rows)}")
    print("  (inspect tree under raw/<user_id>/strava/.../*.json)")


def cmd_db_upsert() -> None:
    """Layer 3: fetch page 1, normalize, upsert into Postgres (service-style connection)."""
    import psycopg2
    from psycopg2 import errors as pg_errors

    from pipeline.adapters import strava
    from pipeline.cardio_upsert import upsert_cardio_events

    _require_env("STRAVA_ACCESS_TOKEN")
    user_id = _require_env("SOMA_USER_ID")
    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if not dsn:
        _die(
            "Set SOMA_DATABASE_URL (preferred) or DATABASE_URL to your Supabase "
            "Postgres connection string (see Dashboard → Database → URI)."
        )

    activities = strava.fetch_strava_activities_page(os.environ["STRAVA_ACCESS_TOKEN"], page=1)
    rows = strava.normalize_strava_activities(activities, user_id)
    if not rows:
        print("db-upsert: no rows to insert (empty page?)")
        return

    try:
        conn = psycopg2.connect(dsn)
    except psycopg2.OperationalError as exc:
        hint = ""
        if "db." in dsn and ".supabase.co" in dsn:
            hint = (
                "\n  Hint: direct `db.*.supabase.co` is often IPv6-only. Use the "
                "**Session pooler** URI from Dashboard → Connect (host `*.pooler.supabase.com`, "
                "user `postgres.<project-ref>`). See docs/plans/local-dev-and-tooling.md."
            )
        _die(f"Postgres connection failed: {exc!s}.{hint}", code=2)
    try:
        with conn:
            with conn.cursor() as cur:
                try:
                    upsert_cardio_events(cur, rows)
                except pg_errors.UndefinedTable as exc:
                    if "cardio_events" in str(exc):
                        _die(
                            "Table public.cardio_events does not exist in this database.\n"
                            "  Apply schema/migrations/0001_initial.sql in Supabase SQL Editor.\n"
                            f"  (Underlying error: {exc})",
                            code=3,
                        )
                    raise
    finally:
        conn.close()

    print("db-upsert: OK")
    print(f"  attempted rows: {len(rows)} (ON CONFLICT DO NOTHING skips duplicates)")
    print("  verify in Supabase SQL editor, e.g.:")
    print(
        "    SELECT source_id, activity_type, event_date, duration_min "
        f"FROM cardio_events WHERE user_id = '{user_id}' "
        "ORDER BY created_at DESC LIMIT 10;"
    )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("live", help="Fetch Strava page 1 + normalize (print summary)")
    sub.add_parser("raw-disk", help="Fetch + write raw JSON under SOMA_RAW_LOCAL_DIR")
    sub.add_parser("db-upsert", help="Fetch + normalize + upsert page 1 to Postgres")

    args = parser.parse_args()
    if args.command == "live":
        cmd_live()
    elif args.command == "raw-disk":
        cmd_raw_disk()
    elif args.command == "db-upsert":
        cmd_db_upsert()
    else:
        parser.error("unknown command")


if __name__ == "__main__":
    main()

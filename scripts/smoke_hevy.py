#!/usr/bin/env python3
"""Local smoke: Hevy live fetch, raw JSON to disk, optional Postgres upsert.

See .env.example and docs/plans/local-dev-and-tooling.md (Phase 3 smoke).
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
    """Layer 1: fetch one page from Hevy, normalize, print summary (no disk, no DB)."""
    from pipeline.adapters import hevy

    _require_env("HEVY_API_KEY")
    user_id = _require_env("SOMA_USER_ID")

    payload = hevy.fetch_hevy_workouts_page(os.environ["HEVY_API_KEY"], page=1)
    rows = hevy.normalize_hevy_list_workouts(payload, user_id)
    pc = payload.get("page_count")
    print("live: OK")
    print(f"  page_count (raw): {pc!r}")
    print(f"  workouts in page: {len(payload.get('workouts') or [])}")
    print(f"  normalized strength_events rows: {len(rows)}")
    if rows:
        print(f"  sample source_id: {rows[0].get('source_id')!r}")
        print(f"  sample event_date: {rows[0].get('event_date')!r}")


def cmd_raw_disk() -> None:
    """Layer 2: fetch + write raw JSON under tmp/ (same key layout as S3)."""
    from pipeline.adapters import hevy

    _require_env("HEVY_API_KEY")
    user_id = _require_env("SOMA_USER_ID")
    raw_root = Path(os.environ.get("SOMA_RAW_LOCAL_DIR", "tmp/soma_raw")).resolve()

    def raw_put(key: str, body: bytes) -> None:
        dest = raw_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    utc = datetime.now(timezone.utc)
    rows = hevy.fetch_and_normalize_from_api(
        user_id,
        os.environ["HEVY_API_KEY"],
        raw_put=raw_put,
        utc_now=utc,
    )
    print("raw-disk: OK")
    print(f"  raw_root: {raw_root}")
    print(f"  normalized rows: {len(rows)}")
    print("  (inspect tree under raw/<user_id>/hevy/.../*.json)")


def cmd_db_upsert() -> None:
    """Layer 3: fetch page 1, normalize, upsert into Postgres (service-style connection; bypasses RLS)."""
    _db_upsert_from_rows(_fetch_page1_rows())


def cmd_backfill() -> None:
    """Historical backfill: paginate all Hevy workout pages, optional raw disk + Postgres upsert."""
    from pipeline.adapters import hevy

    _require_env("HEVY_API_KEY")
    user_id = _require_env("SOMA_USER_ID")
    raw_root = Path(os.environ.get("SOMA_RAW_LOCAL_DIR", "tmp/soma_raw")).resolve()
    write_raw = os.environ.get("SOMA_HEVY_BACKFILL_RAW", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    skip_db = os.environ.get("SOMA_HEVY_BACKFILL_SKIP_DB", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    def raw_put(key: str, body: bytes) -> None:
        if not write_raw:
            return
        dest = raw_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    utc = datetime.now(timezone.utc)
    rows = hevy.fetch_and_normalize_from_api(
        user_id,
        os.environ["HEVY_API_KEY"],
        raw_put=raw_put,
        utc_now=utc,
    )
    if not rows:
        print("backfill: no rows to insert (empty account?)")
        return

    if skip_db:
        print("backfill: OK (raw only, SOMA_HEVY_BACKFILL_SKIP_DB set)")
        print(f"  normalized rows: {len(rows)}")
        if write_raw:
            print(f"  raw_root: {raw_root}")
        return

    _db_upsert_from_rows(rows)
    print("backfill: OK")
    print(f"  normalized rows: {len(rows)} (ON CONFLICT DO NOTHING skips duplicates)")
    if write_raw:
        print(f"  raw_root: {raw_root}")
    print("  verify in Supabase SQL editor, e.g.:")
    print(
        "    SELECT COUNT(*) FROM strength_events "
        f"WHERE user_id = '{user_id}' AND source = 'hevy';"
    )


def _fetch_page1_rows() -> list[dict]:
    from pipeline.adapters import hevy

    _require_env("HEVY_API_KEY")
    user_id = _require_env("SOMA_USER_ID")
    payload = hevy.fetch_hevy_workouts_page(os.environ["HEVY_API_KEY"], page=1)
    return hevy.normalize_hevy_list_workouts(payload, user_id)


def _db_upsert_from_rows(rows: list[dict]) -> None:
    import psycopg2
    from psycopg2 import errors as pg_errors

    from pipeline.strength_upsert import upsert_strength_events

    user_id = _require_env("SOMA_USER_ID")
    if not rows:
        print("db-upsert: no rows to insert (empty page?)")
        return

    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if not dsn:
        _die(
            "Set SOMA_DATABASE_URL (preferred) or DATABASE_URL to your Supabase "
            "Postgres connection string (see Dashboard → Database → URI)."
        )

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
                    upsert_strength_events(cur, rows)
                except pg_errors.UndefinedTable as exc:
                    if "strength_events" in str(exc):
                        _die(
                            "Table public.strength_events does not exist in this database.\n"
                            "  Open the file in your repo, copy ALL SQL, then in Supabase Dashboard → SQL Editor\n"
                            "  paste and Run (Supabase does not fetch this file from Git automatically).\n"
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
        "    SELECT source_id, exercise_name, event_date FROM strength_events "
        f"WHERE user_id = '{user_id}' ORDER BY created_at DESC LIMIT 10;"
    )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("live", help="Fetch Hevy page 1 + normalize (print summary)")
    sub.add_parser("raw-disk", help="Fetch + write raw JSON under SOMA_RAW_LOCAL_DIR")
    sub.add_parser("db-upsert", help="Fetch + normalize + upsert page 1 to Postgres")
    sub.add_parser(
        "backfill",
        help="Paginate all Hevy pages; write raw JSON + upsert strength_events (idempotent)",
    )

    args = parser.parse_args()
    if args.command == "live":
        cmd_live()
    elif args.command == "raw-disk":
        cmd_raw_disk()
    elif args.command == "db-upsert":
        cmd_db_upsert()
    elif args.command == "backfill":
        cmd_backfill()
    else:
        parser.error("unknown command")


if __name__ == "__main__":
    main()

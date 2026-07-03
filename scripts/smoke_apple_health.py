#!/usr/bin/env python3
"""Local smoke: Apple Health export JSON → normalize → raw on disk → optional DB upsert.

Reads a JSON file (Soma daily envelope or Health Auto Export ``data.metrics`` shape).
See docs/plans/apple-health-export.md and docs/plans/local-dev-and-tooling.md.
"""

from __future__ import annotations

import argparse
import json
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
        _die(f"Missing environment variable {name}. See .env.example and docs/plans/apple-health-export.md.")
    return v


def _default_fixture() -> Path:
    return _REPO_ROOT / "tests/fixtures/biometrics/health_export_daily_redacted.json"


def cmd_normalize(path: Path) -> None:
    from pipeline.adapters import apple_health_export

    user_id = os.environ.get("SOMA_USER_ID", "00000000-0000-0000-0000-000000000001").strip()
    body = json.loads(path.read_text(encoding="utf-8"))
    rows = apple_health_export.normalize_apple_health_export_payload(body, user_id=user_id)
    print("normalize: OK")
    print(f"  fixture: {path}")
    print(f"  rows: {len(rows)}")
    for r in rows[:12]:
        print(f"    {r['event_date']} {r['metric']}={r['value']!r} ({r.get('unit')})")
    if len(rows) > 12:
        print(f"    … {len(rows) - 12} more")


def cmd_raw_disk(path: Path) -> None:
    from pipeline.adapters import apple_health_export

    user_id = _require_env("SOMA_USER_ID")
    raw_root = Path(os.environ.get("SOMA_RAW_LOCAL_DIR", "tmp/soma_raw")).resolve()

    def raw_put(key: str, body: bytes) -> None:
        dest = raw_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    body = json.loads(path.read_text(encoding="utf-8"))
    utc = datetime.now(timezone.utc)
    key, rows = apple_health_export.ingest_apple_health_export_webhook(
        user_id, body, raw_put=raw_put, utc_now=utc
    )
    print("raw-disk: OK")
    print(f"  raw_root: {raw_root}")
    print(f"  raw_key: {key}")
    print(f"  normalized rows: {len(rows)}")


def cmd_db_upsert(path: Path) -> None:
    import psycopg2
    from psycopg2 import errors as pg_errors

    from pipeline.adapters import apple_health_export
    from pipeline.adapters import apple_health_workouts
    from pipeline.apple_health_cardio_dedup import filter_near_duplicate_apple_cardio
    from pipeline.apple_hevy_cardio_dedup import filter_apple_strength_cardio_when_hevy_present
    from pipeline.biometrics_upsert import upsert_biometrics
    from pipeline.cardio_upsert import (
        delete_cardio_events_by_source_id,
        upsert_cardio_events,
    )

    user_id = _require_env("SOMA_USER_ID")
    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if not dsn:
        _die(
            "Set SOMA_DATABASE_URL (preferred) or DATABASE_URL to your Supabase "
            "Postgres connection string (see Dashboard → Database → URI)."
        )

    body = json.loads(path.read_text(encoding="utf-8"))
    rows_bio = apple_health_export.normalize_apple_health_export_payload(body, user_id=user_id)
    rows_cardio = apple_health_workouts.normalize_apple_health_cardio_from_payload(body, user_id)
    if not rows_bio and not rows_cardio:
        print("db-upsert: no biometrics or cardio rows (empty payload?)")
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
    rows_cardio_in = rows_cardio
    hevy_dropped = 0
    hub_dropped = 0
    superseded = 0
    try:
        with conn:
            with conn.cursor() as cur:
                try:
                    if rows_bio:
                        upsert_biometrics(cur, rows_bio)
                    if rows_cardio_in:
                        rows_for_db, hevy_dropped = filter_apple_strength_cardio_when_hevy_present(
                            cur, user_id=user_id, cardio_rows=rows_cardio_in
                        )
                        rows_for_db, hub_dropped, superseded_ids = (
                            filter_near_duplicate_apple_cardio(
                                cur, user_id=user_id, cardio_rows=rows_for_db
                            )
                        )
                        superseded = delete_cardio_events_by_source_id(
                            cur, user_id=user_id, source_ids=superseded_ids
                        )
                        upsert_cardio_events(cur, rows_for_db)
                except pg_errors.UndefinedTable as exc:
                    if "biometrics" in str(exc):
                        _die(
                            "Table public.biometrics does not exist in this database.\n"
                            "  Apply schema/migrations/0001_initial.sql in Supabase SQL Editor.\n"
                            f"  (Underlying error: {exc})",
                            code=3,
                        )
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
    print(f"  biometrics rows: {len(rows_bio)}")
    print(f"  cardio_events rows (upserted): {len(rows_cardio_in) - hevy_dropped - hub_dropped}")
    print(f"  cardio_events dropped (Hevy same-day strength dup): {hevy_dropped}")
    print(f"  cardio_events dropped (hub near-dup): {hub_dropped}")
    print(f"  cardio_events superseded (lower-priority stored dup deleted): {superseded}")
    print("  verify in Supabase SQL editor, e.g.:")
    print(
        "    SELECT event_date, metric, value, source FROM biometrics "
        f"WHERE user_id = '{user_id}' ORDER BY event_date DESC, metric LIMIT 20;"
    )
    print(
        "    SELECT event_date, activity_type, duration_min, source FROM cardio_events "
        f"WHERE user_id = '{user_id}' ORDER BY event_date DESC LIMIT 20;"
    )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_norm = sub.add_parser("normalize", help="Parse file + normalize (no disk, no DB)")
    p_norm.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help=f"JSON file (default: {_default_fixture().relative_to(_REPO_ROOT)})",
    )

    p_raw = sub.add_parser("raw-disk", help="Normalize + write raw JSON under SOMA_RAW_LOCAL_DIR")
    p_raw.add_argument("path", nargs="?", type=Path, default=None)

    p_db = sub.add_parser("db-upsert", help="Normalize + upsert biometrics + cardio_events to Postgres")
    p_db.add_argument("path", nargs="?", type=Path, default=None)

    args = parser.parse_args()
    path = args.path
    if path is None:
        path = _default_fixture()
    path = path.resolve()
    if not path.is_file():
        _die(f"Not a file: {path}")

    if args.command == "normalize":
        cmd_normalize(path)
    elif args.command == "raw-disk":
        cmd_raw_disk(path)
    elif args.command == "db-upsert":
        cmd_db_upsert(path)
    else:
        parser.error("unknown command")


if __name__ == "__main__":
    main()

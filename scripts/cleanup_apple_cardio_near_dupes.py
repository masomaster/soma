#!/usr/bin/env python3
"""Remove near-duplicate ``apple_health`` ``cardio_events`` already in Postgres.

Health Sync and multi-writer HealthKit UUIDs can leave duplicate workouts that
ingest-time dedup did not catch (pre-deploy rows or separate POSTs). This script
reuses :func:`pipeline.apple_health_cardio_dedup.apple_cardio_rows_to_drop`.

Examples::

    python scripts/cleanup_apple_cardio_near_dupes.py              # dry-run
    python scripts/cleanup_apple_cardio_near_dupes.py --apply
    python scripts/cleanup_apple_cardio_near_dupes.py --since 2026-06-01 --apply

Requires ``SOMA_USER_ID`` and ``SOMA_DATABASE_URL`` in ``.env`` (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
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
    value = os.environ.get(name, "").strip()
    if not value:
        _die(f"Missing environment variable {name}. See .env.example.")
    return value


def _resolve_dsn() -> str:
    for key in ("SOMA_DATABASE_URL", "DB_CONNECT_STRING", "DATABASE_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    _die("Set SOMA_DATABASE_URL (preferred) or DATABASE_URL in .env.")


def _load_apple_cardio_rows(
    conn: object,
    *,
    user_id: str,
    since: date | None,
) -> list[dict]:
    from psycopg2.extras import RealDictCursor

    sql = (
        "SELECT id, source, source_id, event_date, activity_type, duration_min, "
        "distance_miles, elevation_ft, avg_hr, max_hr, calories "
        "FROM cardio_events WHERE user_id = %s::uuid AND source = %s"
    )
    params: list[object] = [user_id, "apple_health"]
    if since is not None:
        sql += " AND event_date >= %s"
        params.append(since)
    sql += " ORDER BY event_date DESC, source_id"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete duplicate rows (default: dry-run only)",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only consider rows on or after YYYY-MM-DD",
    )
    args = parser.parse_args()

    user_id = _require_env("SOMA_USER_ID")
    dsn = _resolve_dsn()
    since = date.fromisoformat(args.since) if args.since else None

    import psycopg2

    from pipeline.apple_health_cardio_dedup import apple_cardio_rows_to_drop

    conn = psycopg2.connect(dsn)
    try:
        rows = _load_apple_cardio_rows(conn, user_id=user_id, since=since)
        drops = apple_cardio_rows_to_drop(rows)
        if not drops:
            print("No near-duplicate apple_health cardio rows found.")
            return

        print(f"Found {len(drops)} near-duplicate row(s) to remove:")
        for row in drops:
            print(
                f"  {row.get('event_date')} {row.get('activity_type')} "
                f"{row.get('duration_min')} min — {row.get('source_id')}"
            )

        if not args.apply:
            print("Dry-run only. Re-run with --apply to delete.")
            return

        with conn.cursor() as cur:
            ids = [str(row["id"]) for row in drops]
            cur.execute(
                "DELETE FROM cardio_events WHERE user_id = %s::uuid AND id = ANY(%s::uuid[])",
                (user_id, ids),
            )
        conn.commit()
        print(
            f"Deleted {len(drops)} row(s). "
            "Re-run the daily pipeline to refresh daily_features cardio totals."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

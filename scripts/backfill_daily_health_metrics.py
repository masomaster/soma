"""Promote existing ``biometrics`` into ``daily_health_metrics`` (dashboard path).

Use after Apple Health / HAE ingest was writing EAV rows but the wide table was
only updated by the morning briefing (often before sleep landed). Safe to re-run:
sparse ``ON CONFLICT DO UPDATE`` overwrites metric columns for each day.

  python scripts/backfill_daily_health_metrics.py
  python scripts/backfill_daily_health_metrics.py --since 2026-07-01
  python scripts/backfill_daily_health_metrics.py --dry-run
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
    v = os.environ.get(name, "").strip()
    if not v:
        _die(f"Missing environment variable {name}.")
    return v


def _parse_date(raw: str | None) -> date | None:
    if raw is None or not raw.strip():
        return None
    return date.fromisoformat(raw.strip())


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only roll up event_date >= this ISO date (inclusive)",
    )
    parser.add_argument(
        "--until",
        type=str,
        default=None,
        help="Only roll up event_date <= this ISO date (inclusive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List dates that would be rolled up; do not write",
    )
    args = parser.parse_args()

    user_id = _require_env("SOMA_USER_ID")
    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if not dsn:
        _die("Set SOMA_DATABASE_URL or DATABASE_URL.")

    since = _parse_date(args.since)
    until = _parse_date(args.until)

    import psycopg2

    from pipeline.biometrics_daily_rollup import (
        list_biometrics_event_dates,
        rollup_biometrics_dates,
    )

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                dates = list_biometrics_event_dates(
                    cur, user_id=user_id, since=since, until=until
                )
                if not dates:
                    print("backfill: no biometrics event_date values in range")
                    return
                print(f"backfill: {len(dates)} day(s) from {dates[0]} .. {dates[-1]}")
                if args.dry_run:
                    for d in dates:
                        print(f"  would roll up {d.isoformat()}")
                    print("backfill: dry-run (no writes)")
                    return
                rolled = rollup_biometrics_dates(cur, user_id=user_id, dates=dates)
                print(f"backfill: OK — upserted {len(rolled)} daily_health_metrics day(s)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

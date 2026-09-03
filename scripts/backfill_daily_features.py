"""Recompute ``daily_features`` (readiness / sleep debt / ACWR) from wide metrics.

Run after ``backfill_daily_health_metrics.py`` (or whenever recovery charts are
stale). Does **not** call the LLM — dashboard-ready only.

  python scripts/backfill_daily_features.py
  python scripts/backfill_daily_features.py --since 2026-07-01
  python scripts/backfill_daily_features.py --dry-run
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
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--until", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
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

    from pipeline.daily_features_recompute import (
        list_daily_health_metric_dates,
        recompute_daily_features,
    )

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                dates = list_daily_health_metric_dates(
                    cur, user_id=user_id, since=since, until=until
                )
                if not dates:
                    print("backfill: no daily_health_metrics dates in range")
                    return
                print(f"backfill: {len(dates)} day(s) from {dates[0]} .. {dates[-1]}")
                if args.dry_run:
                    for d in dates[:20]:
                        print(f"  would recompute {d.isoformat()}")
                    if len(dates) > 20:
                        print(f"  … {len(dates) - 20} more")
                    print("backfill: dry-run (no writes)")
                    return
                rows = recompute_daily_features(cur, user_id=user_id, dates=dates)
                with_ready = sum(
                    1 for r in rows if r.get("overall_readiness_score") is not None
                )
                print(
                    f"backfill: OK — upserted {len(rows)} daily_features day(s) "
                    f"({with_ready} with readiness)"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

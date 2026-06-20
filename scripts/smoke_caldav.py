#!/usr/bin/env python3
"""Local smoke: list iCloud CalDAV calendars and fetch busy blocks → interventions.

Requires ``caldav`` (``pip install caldav`` or Lambda layer). Loads ``.env`` like other smokes.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
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


def _creds() -> tuple[str, str, str]:
    from pipeline.lambda_secrets import resolve_caldav_credentials

    return resolve_caldav_credentials()


def _window() -> tuple[date, date]:
    back = int(os.environ.get("CALDAV_LOOKBACK_DAYS", "7"))
    forward = int(os.environ.get("CALDAV_LOOKAHEAD_DAYS", "30"))
    today = date.today()
    return today - timedelta(days=back), today + timedelta(days=forward)


def cmd_list_calendars() -> None:
    from pipeline.adapters.caldav_calendar import list_caldav_calendars

    url, username, password = _creds()
    start, end = _window()
    rows = list_caldav_calendars(
        url=url, username=username, password=password, start=start, end=end
    )
    print(f"list-calendars: window {start} .. {end}")
    for row in rows:
        count = row["event_count"]
        suffix = " (search error)" if count < 0 else ""
        print(f"  {row['name']!r}: {count} events{suffix}")
    filter_name = os.environ.get("CALDAV_CALENDAR_NAME", "").strip()
    if filter_name:
        print(f"  filter CALDAV_CALENDAR_NAME={filter_name!r}")


def cmd_fetch() -> None:
    from pipeline.adapters.caldav_calendar import fetch_caldav_events, normalize_caldav_events

    user_id = os.environ.get("SOMA_USER_ID", "").strip()
    if not user_id:
        _die("Set SOMA_USER_ID in .env")

    url, username, password = _creds()
    start, end = _window()
    calendar_name = os.environ.get("CALDAV_CALENDAR_NAME", "").strip() or None
    events = fetch_caldav_events(
        url=url,
        username=username,
        password=password,
        start=start,
        end=end,
        calendar_name=calendar_name,
    )
    rows = normalize_caldav_events(events, user_id)
    print("fetch: OK")
    print(f"  calendar filter: {calendar_name!r}")
    print(f"  parsed events: {len(events)}")
    print(f"  intervention rows: {len(rows)}")
    for r in rows[:10]:
        print(f"    {r['event_date']} {r['description'][:60]!r}")
    if len(rows) > 10:
        print(f"    … {len(rows) - 10} more")


def cmd_db_upsert() -> None:
    from datetime import datetime, timezone

    from pipeline.adapters.caldav_calendar import fetch_caldav_events
    from pipeline.caldav_scheduled_ingest import run_caldav_scheduled_ingest

    user_id = os.environ.get("SOMA_USER_ID", "").strip()
    dsn = os.environ.get("SOMA_DATABASE_URL", "").strip()
    if not user_id:
        _die("Set SOMA_USER_ID in .env")
    if not dsn:
        _die("Set SOMA_DATABASE_URL in .env")

    url, username, password = _creds()
    start, end = _window()
    calendar_name = os.environ.get("CALDAV_CALENDAR_NAME", "").strip() or None

    def fetch_events():
        return fetch_caldav_events(
            url=url,
            username=username,
            password=password,
            start=start,
            end=end,
            calendar_name=calendar_name,
        )

    raw_root = Path(os.environ.get("SOMA_RAW_LOCAL_DIR", "tmp/soma_raw")).resolve()

    def raw_put(key: str, body: bytes) -> None:
        dest = raw_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    result = run_caldav_scheduled_ingest(
        user_id=user_id,
        dsn=dsn,
        raw_put=raw_put,
        utc_now=datetime.now(timezone.utc),
        fetch_events=fetch_events,
    )
    print(f"db-upsert: OK ({result['intervention_rows']} rows)")


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-calendars", help="Print each calendar name + event count in window")
    sub.add_parser("fetch", help="Fetch + normalize (no DB)")
    sub.add_parser("db-upsert", help="Fetch + insert interventions")
    args = parser.parse_args()
    if args.command == "list-calendars":
        cmd_list_calendars()
    elif args.command == "fetch":
        cmd_fetch()
    elif args.command == "db-upsert":
        cmd_db_upsert()
    else:
        parser.error("unknown command")


if __name__ == "__main__":
    main()

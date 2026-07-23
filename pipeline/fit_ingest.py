"""CLI / library entry: ingest Dropbox FIT (recurring) or Strava export (one-shot).

**``wahoo_fit``** — scheduled/recurring ingest of new BOLT rides from a Dropbox
sync folder.

**``strava_export``** — **one-time** historical backfill from a Strava bulk
archive. Do not cron this source.

Example (ongoing)::

    python -m pipeline.fit_ingest --user-id UUID --source wahoo_fit \\
        --dir ~/Dropbox/Apps/Wahoo --estimate-ftp

Requires DB env (same as other Soma local tools) when persisting; pass
``--dry-run`` to parse only.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.adapters import fit_activity
from pipeline.adapters.fit_activity import (
    SOURCE_STRAVA_EXPORT,
    SOURCE_WAHOO_FIT,
    discover_activity_files,
    fetch_and_normalize,
    load_strava_activity_titles,
    sha256_hex,
)
from pipeline.cardio_upsert import delete_cardio_events_by_source_id, upsert_cardio_events
from pipeline.ftp_estimate import estimate_and_persist_ftp
from pipeline.power_cardio_dedup import (
    filter_power_cardio_duplicates,
    load_existing_cardio_for_dates,
)

logger = logging.getLogger(__name__)


def _noop_raw_put(key: str, body: bytes) -> None:
    logger.debug("dry-run raw_put %s (%d bytes)", key, len(body))


def ingest_activity_directory(
    *,
    user_id: str,
    source: str,
    directory: Path,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime | None = None,
    seen_sha256: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse all activity files under ``directory``; return ``(rows, skipped_sha256)``.

    Does not touch the database. ``seen_sha256`` short-circuits already-ingested
    payloads when provided (caller maintains the set across runs / DB lookup).
    """
    if source not in {SOURCE_WAHOO_FIT, SOURCE_STRAVA_EXPORT}:
        raise ValueError(f"unsupported source {source!r}")
    now = utc_now or datetime.now(timezone.utc)
    titles = load_strava_activity_titles(directory) if source == SOURCE_STRAVA_EXPORT else {}
    files = discover_activity_files(directory)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen = seen_sha256 if seen_sha256 is not None else set()
    for path in files:
        payload = path.read_bytes()
        digest = sha256_hex(fit_activity.maybe_gunzip(payload))
        if digest in seen:
            skipped.append(digest)
            continue
        notes = titles.get(path.name) or titles.get(path.stem)
        try:
            batch = fetch_and_normalize(
                user_id,
                source=source,
                filename=path.name,
                payload=payload,
                raw_put=raw_put,
                utc_now=now,
                notes=notes,
            )
        except fit_activity.FitDecodeUnavailableError:
            raise
        except Exception as exc:
            logger.warning("Skipping %s: %s: %s", path, type(exc).__name__, exc)
            continue
        seen.add(digest)
        rows.extend(batch)
        # Nudge timestamps so raw keys stay unique within a batch.
        now = now.replace(microsecond=(now.microsecond + 1) % 1_000_000)
        if now.microsecond == 0:
            from datetime import timedelta

            now = now + timedelta(seconds=1)
    return rows, skipped


def persist_ingested_rows(
    cur: Any,
    *,
    user_id: str,
    rows: list[dict[str, Any]],
) -> tuple[int, int]:
    """Dedup against existing cardio, delete superseded, upsert. Returns counts."""
    if not rows:
        return 0, 0
    dates = sorted({r["event_date"] for r in rows if isinstance(r.get("event_date"), date)})
    existing = load_existing_cardio_for_dates(cur, user_id=user_id, dates=dates)
    kept, superseded = filter_power_cardio_duplicates(rows, existing)
    deleted = delete_cardio_events_by_source_id(cur, user_id=user_id, source_ids=superseded)
    upsert_cardio_events(cur, kept)
    return len(kept), deleted


def _connect_pg() -> Any:
    """Open a psycopg2 connection from ``DATABASE_URL`` or Supabase-style env."""
    import psycopg2

    url = os.environ.get("DATABASE_URL") or os.environ.get("SOMA_DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL (or SOMA_DATABASE_URL) to persist ingest")
    return psycopg2.connect(url)


def _s3_raw_put_from_env() -> Callable[[str, bytes], None] | None:
    bucket = os.environ.get("SOMA_RAW_BUCKET") or os.environ.get("RAW_BUCKET")
    if not bucket:
        return None
    import boto3

    client = boto3.client("s3")

    def put(key: str, body: bytes) -> None:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    return put


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest cycling activity files: wahoo_fit=recurring Dropbox FIT; "
            "strava_export=one-time Strava archive backfill"
        )
    )
    parser.add_argument("--user-id", required=True, help="Soma user UUID")
    parser.add_argument(
        "--source",
        required=True,
        choices=[SOURCE_WAHOO_FIT, SOURCE_STRAVA_EXPORT],
        help=(
            "wahoo_fit: recurring Dropbox/BOLT FITs; "
            "strava_export: one-time historical Strava archive (not scheduled)"
        ),
    )
    parser.add_argument(
        "--dir",
        required=True,
        type=Path,
        help="Dropbox sync folder (wahoo_fit) or unzipped Strava export root (strava_export)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print row counts without DB/S3 writes",
    )
    parser.add_argument(
        "--estimate-ftp",
        action="store_true",
        help="After ingest, recompute FTP onto daily_health_metrics",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    raw_put: Callable[[str, bytes], None]
    if args.dry_run:
        raw_put = _noop_raw_put
    else:
        raw_put = _s3_raw_put_from_env() or _noop_raw_put
        if raw_put is _noop_raw_put:
            logger.warning("SOMA_RAW_BUCKET unset; raw envelopes only logged locally")

    rows, skipped = ingest_activity_directory(
        user_id=args.user_id,
        source=args.source,
        directory=args.dir,
        raw_put=raw_put,
    )
    logger.info(
        "Parsed %d cardio row(s); skipped %d already-seen sha256",
        len(rows),
        len(skipped),
    )
    if args.dry_run:
        for r in rows[:20]:
            logger.info(
                "  %s %s %s avg_w=%s mmp_keys=%s",
                r.get("event_date"),
                r.get("activity_type"),
                r.get("source_id"),
                r.get("avg_watts"),
                list((r.get("power_mmp_json") or {}).keys()),
            )
        return 0

    conn = _connect_pg()
    try:
        with conn.cursor() as cur:
            kept, deleted = persist_ingested_rows(cur, user_id=args.user_id, rows=rows)
            logger.info("Upserted %d row(s); deleted %d superseded", kept, deleted)
            if args.estimate_ftp:
                est = estimate_and_persist_ftp(
                    cur, user_id=args.user_id, as_of=date.today()
                )
                logger.info("FTP estimate: %s", est)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

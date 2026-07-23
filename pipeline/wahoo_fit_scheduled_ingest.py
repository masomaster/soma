"""Scheduled Wahoo FIT pull via Dropbox API → raw S3 → ``cardio_events`` + FTP."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg2

from pipeline.adapters.fit_activity import (
    SOURCE_WAHOO_FIT,
    FitDecodeUnavailableError,
    fetch_and_normalize,
)
from pipeline.cardio_upsert import delete_cardio_events_by_source_id, upsert_cardio_events
from pipeline.dropbox_api import (
    DropboxApiError,
    download_file,
    is_activity_filename,
    iter_folder_files,
    refresh_access_token,
)
from pipeline.ftp_estimate import estimate_and_persist_ftp
from pipeline.power_cardio_dedup import (
    filter_power_cardio_duplicates,
    load_existing_cardio_for_dates,
)

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 45
DEFAULT_MAX_FILES = 80


def _parse_dropbox_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _nudge_utc(now: datetime) -> datetime:
    """Advance timestamp so successive raw S3 keys stay unique within a batch."""
    nxt = now.replace(microsecond=(now.microsecond + 1) % 1_000_000)
    if nxt.microsecond == 0:
        return nxt + timedelta(seconds=1)
    return nxt


def run_wahoo_fit_dropbox_ingest(
    *,
    user_id: str,
    app_key: str,
    app_secret: str,
    refresh_token: str,
    folder_path: str,
    dsn: str,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    estimate_ftp: bool = True,
    as_of: date | None = None,
    lookback_days: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    """List Dropbox activity files, normalize each, upsert, optionally estimate FTP.

    Only files with ``client_modified`` within ``lookback_days`` are downloaded
    (default 45). Entries with missing timestamps are kept. Listing is
    non-recursive (Wahoo FITs sit in the folder root). At most ``max_files``
    (default 80) are processed per invocation to stay under Lambda timeout.

    Dropbox / storage / missing-``fitdecode`` errors fail the job. Per-file
    parse failures are skipped and counted in ``errors``.
    """
    if lookback_days is None:
        raw = os.environ.get("SOMA_DROPBOX_LOOKBACK_DAYS", "").strip()
        lookback_days = int(raw) if raw.isdigit() else DEFAULT_LOOKBACK_DAYS
    if max_files is None:
        raw_max = os.environ.get("SOMA_DROPBOX_MAX_FILES", "").strip()
        max_files = int(raw_max) if raw_max.isdigit() else DEFAULT_MAX_FILES
    max_files = max(1, max_files)
    cutoff = utc_now.astimezone(timezone.utc) - timedelta(days=max(1, lookback_days))

    access = refresh_access_token(
        app_key=app_key, app_secret=app_secret, refresh_token=refresh_token
    )
    entries = []
    skipped_old = 0
    for e in iter_folder_files(
        access_token=access, folder_path=folder_path, recursive=False
    ):
        if not is_activity_filename(e.name):
            continue
        modified = _parse_dropbox_modified(e.client_modified)
        if modified is not None and modified < cutoff:
            skipped_old += 1
            continue
        entries.append(e)
        if len(entries) >= max_files:
            break
    logger.info(
        "Dropbox listed %d recent activity file(s) under %r (skipped_old=%d lookback=%d max=%d) for user %s",
        len(entries),
        folder_path or "/",
        skipped_old,
        lookback_days,
        max_files,
        user_id,
    )

    rows: list[dict[str, Any]] = []
    errors = 0
    now = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    for entry in entries:
        try:
            payload = download_file(access_token=access, path=entry.path_lower)
            batch = fetch_and_normalize(
                user_id,
                source=SOURCE_WAHOO_FIT,
                filename=entry.name,
                payload=payload,
                raw_put=raw_put,
                utc_now=now,
            )
            rows.extend(batch)
            now = _nudge_utc(now)
        except (DropboxApiError, FitDecodeUnavailableError):
            raise
        except Exception as exc:
            mod = type(exc).__module__ or ""
            # S3 / botocore failures must fail the Lambda so Scheduler alarms fire.
            if mod.startswith("botocore") or mod.startswith("urllib"):
                raise
            errors += 1
            logger.warning(
                "Skipping Dropbox file %s: %s: %s",
                entry.path_display or entry.name,
                type(exc).__name__,
                exc,
            )

    kept = 0
    deleted = 0
    ftp_result: dict[str, Any] | None = None
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                if rows:
                    dates = sorted(
                        {r["event_date"] for r in rows if isinstance(r.get("event_date"), date)}
                    )
                    existing = load_existing_cardio_for_dates(
                        cur, user_id=user_id, dates=dates
                    )
                    to_upsert, superseded = filter_power_cardio_duplicates(rows, existing)
                    deleted = delete_cardio_events_by_source_id(
                        cur, user_id=user_id, source_ids=superseded
                    )
                    upsert_cardio_events(cur, to_upsert)
                    kept = len(to_upsert)
                if estimate_ftp:
                    ftp_result = estimate_and_persist_ftp(
                        cur, user_id=user_id, as_of=as_of or utc_now.date()
                    )
    finally:
        conn.close()

    result = {
        "ok": True,
        "listed": len(entries),
        "skipped_old": skipped_old,
        "parsed_rows": len(rows),
        "upserted": kept,
        "superseded_deleted": deleted,
        "errors": errors,
        "ftp": ftp_result,
    }
    logger.info("Wahoo Dropbox ingest finished %s", result)
    return result

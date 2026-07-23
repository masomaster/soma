"""Hermetic tests for Dropbox-backed Wahoo FIT scheduled ingest."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest import mock

import pytest

from pipeline.dropbox_api import DropboxApiError, DropboxFileEntry
from pipeline.raw_storage import format_raw_object_key
from pipeline.wahoo_fit_scheduled_ingest import run_wahoo_fit_dropbox_ingest


def _conn_with_cursor() -> mock.MagicMock:
    live_conn = mock.MagicMock()
    cur = mock.MagicMock()
    cursor_cm = mock.MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = False
    live_conn.cursor.return_value = cursor_cm
    live_conn.__enter__.return_value = live_conn
    live_conn.__exit__.return_value = False
    return live_conn


def test_run_wahoo_fit_dropbox_ingest_happy_path() -> None:
    entry = DropboxFileEntry(
        name="ride.fit",
        path_display="/Apps/WahooFitness/ride.fit",
        path_lower="/apps/wahoofitness/ride.fit",
        content_hash="abc",
        client_modified="2026-07-20T12:00:00Z",
    )
    row = {
        "user_id": "u1",
        "source": "wahoo_fit",
        "source_id": "sha:1",
        "event_date": date(2026, 7, 20),
        "avg_watts": 180,
    }
    live_conn = _conn_with_cursor()

    with (
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.refresh_access_token",
            return_value="access",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.iter_folder_files",
            return_value=[entry],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.download_file",
            return_value=b"fitbytes",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.fetch_and_normalize",
            return_value=[row],
        ) as fetch_mock,
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.psycopg2.connect",
            return_value=live_conn,
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.load_existing_cardio_for_dates",
            return_value=[],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.filter_power_cardio_duplicates",
            return_value=([row], []),
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.delete_cardio_events_by_source_id",
            return_value=0,
        ),
        mock.patch("pipeline.wahoo_fit_scheduled_ingest.upsert_cardio_events") as upsert,
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.estimate_and_persist_ftp",
            return_value={"ftp_watts": 150.0},
        ) as ftp,
    ):
        result = run_wahoo_fit_dropbox_ingest(
            user_id="u1",
            app_key="k",
            app_secret="s",
            refresh_token="rt",
            folder_path="/Apps/WahooFitness",
            dsn="postgres://x",
            raw_put=lambda _k, _b: None,
            utc_now=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            estimate_ftp=True,
        )

    assert result["ok"] is True
    assert result["listed"] == 1
    assert result["upserted"] == 1
    assert result["ftp"]["ftp_watts"] == 150.0
    fetch_mock.assert_called_once()
    upsert.assert_called_once()
    ftp.assert_called_once()
    live_conn.close.assert_called_once()


def test_multi_file_uses_distinct_utc_now_for_raw_keys() -> None:
    entries = [
        DropboxFileEntry(
            name="a.fit",
            path_display="/Apps/WahooFitness/a.fit",
            path_lower="/apps/wahoofitness/a.fit",
            content_hash="h1",
            client_modified="2026-07-20T12:00:00Z",
        ),
        DropboxFileEntry(
            name="b.fit",
            path_display="/Apps/WahooFitness/b.fit",
            path_lower="/apps/wahoofitness/b.fit",
            content_hash="h2",
            client_modified="2026-07-21T12:00:00Z",
        ),
    ]
    stamps: list[datetime] = []

    def fake_fetch(_user_id, **kwargs):  # type: ignore[no-untyped-def]
        stamps.append(kwargs["utc_now"])
        return [
            {
                "user_id": "u1",
                "source": "wahoo_fit",
                "source_id": f"sha:{kwargs['filename']}",
                "event_date": date(2026, 7, 20),
            }
        ]

    live_conn = _conn_with_cursor()
    with (
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.refresh_access_token",
            return_value="access",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.iter_folder_files",
            return_value=entries,
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.download_file",
            return_value=b"fit",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.fetch_and_normalize",
            side_effect=fake_fetch,
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.psycopg2.connect",
            return_value=live_conn,
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.load_existing_cardio_for_dates",
            return_value=[],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.filter_power_cardio_duplicates",
            side_effect=lambda rows, _existing: (rows, []),
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.delete_cardio_events_by_source_id",
            return_value=0,
        ),
        mock.patch("pipeline.wahoo_fit_scheduled_ingest.upsert_cardio_events"),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.estimate_and_persist_ftp",
            return_value={},
        ),
    ):
        run_wahoo_fit_dropbox_ingest(
            user_id="u1",
            app_key="k",
            app_secret="s",
            refresh_token="rt",
            folder_path="/Apps/WahooFitness",
            dsn="postgres://x",
            raw_put=lambda _k, _b: None,
            utc_now=datetime(2026, 7, 23, 12, 0, 0, 0, tzinfo=timezone.utc),
            estimate_ftp=False,
        )

    assert len(stamps) == 2
    assert stamps[0] != stamps[1]
    keys = {format_raw_object_key("u1", "wahoo_fit", t) for t in stamps}
    assert len(keys) == 2


def test_dropbox_download_error_fails_job() -> None:
    entry = DropboxFileEntry(
        name="ride.fit",
        path_display="/Apps/WahooFitness/ride.fit",
        path_lower="/apps/wahoofitness/ride.fit",
        content_hash="abc",
        client_modified="2026-07-20T12:00:00Z",
    )
    with (
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.refresh_access_token",
            return_value="access",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.iter_folder_files",
            return_value=[entry],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.download_file",
            side_effect=DropboxApiError("boom", status_code=500),
        ),
        mock.patch("pipeline.wahoo_fit_scheduled_ingest.psycopg2.connect") as connect,
    ):
        with pytest.raises(DropboxApiError, match="boom"):
            run_wahoo_fit_dropbox_ingest(
                user_id="u1",
                app_key="k",
                app_secret="s",
                refresh_token="rt",
                folder_path="/Apps/WahooFitness",
                dsn="postgres://x",
                raw_put=lambda _k, _b: None,
                utc_now=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
                estimate_ftp=False,
            )
    connect.assert_not_called()


def test_lookback_skips_old_files() -> None:
    old = DropboxFileEntry(
        name="old.fit",
        path_display="/Apps/WahooFitness/old.fit",
        path_lower="/apps/wahoofitness/old.fit",
        content_hash="o",
        client_modified="2026-01-01T12:00:00Z",
    )
    recent = DropboxFileEntry(
        name="new.fit",
        path_display="/Apps/WahooFitness/new.fit",
        path_lower="/apps/wahoofitness/new.fit",
        content_hash="n",
        client_modified="2026-07-20T12:00:00Z",
    )
    live_conn = _conn_with_cursor()
    with (
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.refresh_access_token",
            return_value="access",
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.iter_folder_files",
            return_value=[old, recent],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.download_file",
            return_value=b"fit",
        ) as download,
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.fetch_and_normalize",
            return_value=[],
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.psycopg2.connect",
            return_value=live_conn,
        ),
        mock.patch(
            "pipeline.wahoo_fit_scheduled_ingest.estimate_and_persist_ftp",
            return_value={},
        ),
    ):
        result = run_wahoo_fit_dropbox_ingest(
            user_id="u1",
            app_key="k",
            app_secret="s",
            refresh_token="rt",
            folder_path="/Apps/WahooFitness",
            dsn="postgres://x",
            raw_put=lambda _k, _b: None,
            utc_now=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            lookback_days=45,
            estimate_ftp=False,
        )
    assert result["listed"] == 1
    assert result["skipped_old"] == 1
    download.assert_called_once_with(
        access_token="access", path="/apps/wahoofitness/new.fit"
    )

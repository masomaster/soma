"""Tests for directory ingest orchestration (no DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline.adapters.fit_activity import SOURCE_WAHOO_FIT
from pipeline.fit_ingest import ingest_activity_directory

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "fit"


def test_ingest_directory_parses_fit(tmp_path: Path) -> None:
    pytest.importorskip("fitdecode")
    src = _FIXTURE_DIR / "ride_power_20min.fit"
    dest = tmp_path / "ride_power_20min.fit"
    dest.write_bytes(src.read_bytes())
    stored: list[str] = []

    def raw_put(key: str, body: bytes) -> None:
        stored.append(key)

    rows, skipped = ingest_activity_directory(
        user_id="00000000-0000-0000-0000-000000000001",
        source=SOURCE_WAHOO_FIT,
        directory=tmp_path,
        raw_put=raw_put,
        utc_now=datetime(2024, 6, 1, 16, 0, 0, tzinfo=timezone.utc),
    )
    assert skipped == []
    assert len(rows) == 1
    assert rows[0]["avg_watts"] is not None
    assert stored and stored[0].endswith(".json")

    # Second pass with seen set skips by sha256
    seen: set[str] = set()
    rows1, _ = ingest_activity_directory(
        user_id="00000000-0000-0000-0000-000000000001",
        source=SOURCE_WAHOO_FIT,
        directory=tmp_path,
        raw_put=raw_put,
        seen_sha256=seen,
    )
    assert len(rows1) == 1
    rows2, skipped2 = ingest_activity_directory(
        user_id="00000000-0000-0000-0000-000000000001",
        source=SOURCE_WAHOO_FIT,
        directory=tmp_path,
        raw_put=raw_put,
        seen_sha256=seen,
    )
    assert rows2 == []
    assert len(skipped2) == 1

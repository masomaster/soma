"""Tests for FIT/TCX/GPX activity adapter and power dedup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline.adapters import fit_activity
from pipeline.adapters.fit_activity import (
    SOURCE_WAHOO_FIT,
    fetch_and_normalize,
    parse_activity_bytes,
    parse_tcx,
    session_to_cardio_row,
    sha256_hex,
)
from pipeline.power_cardio_dedup import filter_power_cardio_duplicates, near_duplicate_power_cardio
from pipeline.power_source_priority import power_cardio_source_rank

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fit" / "ride_power_20min.fit"
_USER = "00000000-0000-0000-0000-000000000001"


def test_power_source_rank_order() -> None:
    assert power_cardio_source_rank("wahoo_fit") > power_cardio_source_rank("strava_export")
    assert power_cardio_source_rank("strava_export") > power_cardio_source_rank("apple_health")


def test_parse_fit_fixture_and_normalize() -> None:
    pytest.importorskip("fitdecode")
    data = _FIXTURE.read_bytes()
    session = parse_activity_bytes(data, filename="ride_power_20min.fit")
    assert session["started_at"] is not None
    assert len(session["watts"]) >= 1200
    row = session_to_cardio_row(session, user_id=_USER, source=SOURCE_WAHOO_FIT)
    assert row is not None
    assert row["source"].startswith("wahoo")
    assert row["avg_watts"] is not None
    assert row["power_mmp_json"] is not None
    assert row["power_mmp_json"]["1200"] == 250.0
    assert row["quality_flags"] is None


def test_fetch_and_normalize_writes_json_envelope() -> None:
    pytest.importorskip("fitdecode")
    data = _FIXTURE.read_bytes()
    stored: list[tuple[str, bytes]] = []

    def raw_put(key: str, body: bytes) -> None:
        stored.append((key, body))

    rows = fetch_and_normalize(
        _USER,
        source=SOURCE_WAHOO_FIT,
        filename="ride_power_20min.fit",
        payload=data,
        raw_put=raw_put,
        utc_now=datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert len(stored) == 1
    key, body = stored[0]
    assert key.endswith(".json")
    assert b"payload_base64" in body
    assert b"sha256" in body


def test_tcx_with_watts() -> None:
    tcx = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase>
  <Activities>
    <Activity Sport="Biking">
      <Id>2024-06-02T10:00:00Z</Id>
      <Lap>
        <Track>
          <Trackpoint>
            <Time>2024-06-02T10:00:00Z</Time>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>120</Value></HeartRateBpm>
            <Extensions><TPX><Watts>180</Watts></TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2024-06-02T10:00:30Z</Time>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>130</Value></HeartRateBpm>
            <Extensions><TPX><Watts>200</Watts></TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2024-06-02T10:01:00Z</Time>
            <DistanceMeters>400</DistanceMeters>
            <HeartRateBpm><Value>135</Value></HeartRateBpm>
            <Extensions><TPX><Watts>220</Watts></TPX></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""
    session = parse_tcx(tcx)
    assert session["activity_type"] == "Ride"
    assert session["started_at"] is not None
    assert any(w is not None for w in session["watts"])
    row = session_to_cardio_row(session, user_id=_USER, source="strava_export")
    assert row is not None
    assert row["avg_watts"] is not None


def test_source_id_stable() -> None:
    start = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    a = fit_activity.activity_source_id(
        SOURCE_WAHOO_FIT, started_at=start, activity_type="Ride", duration_sec=3600
    )
    b = fit_activity.activity_source_id(
        SOURCE_WAHOO_FIT, started_at=start, activity_type="Ride", duration_sec=3600
    )
    assert a == b
    assert a.startswith("wahoo_fit:")


def test_sha256_hex() -> None:
    assert len(sha256_hex(b"abc")) == 64


def test_near_dup_and_fit_wins_over_apple() -> None:
    start = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    apple = {
        "source": "apple_health",
        "source_app": "Strava",
        "source_id": "apple:1",
        "event_date": start.date(),
        "started_at": start,
        "activity_type": "Outdoor Cycling",
        "duration_min": 60.0,
        "distance_miles": 15.0,
        "avg_hr": 140,
    }
    fit_row = {
        "source": "wahoo_fit",
        "source_id": "wahoo_fit:abc",
        "event_date": start.date(),
        "started_at": start,
        "activity_type": "Ride",
        "duration_min": 60.0,
        "distance_miles": 15.1,
        "avg_watts": 220.0,
        "power_mmp_json": {"1200": 250},
    }
    assert near_duplicate_power_cardio(apple, fit_row)
    kept, superseded = filter_power_cardio_duplicates([fit_row], [apple])
    assert len(kept) == 1
    assert kept[0]["source"] == "wahoo_fit"
    assert superseded == ["apple:1"]


def test_existing_wahoo_blocks_weaker_strava_export() -> None:
    start = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    existing = {
        "source": "wahoo_fit",
        "source_id": "wahoo_fit:1",
        "event_date": start.date(),
        "started_at": start,
        "activity_type": "Ride",
        "duration_min": 60.0,
        "avg_watts": 230.0,
        "power_mmp_json": {"1200": 260},
    }
    incoming = {
        "source": "strava_export",
        "source_id": "strava_export:1",
        "event_date": start.date(),
        "started_at": start,
        "activity_type": "Ride",
        "duration_min": 60.0,
        "avg_watts": 220.0,
        "power_mmp_json": {"1200": 250},
    }
    kept, superseded = filter_power_cardio_duplicates([incoming], [existing])
    assert kept == []
    assert superseded == []

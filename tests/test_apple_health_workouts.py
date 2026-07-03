"""HAE workouts → cardio_events normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.adapters import apple_health_export
from pipeline.adapters import apple_health_workouts

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "biometrics"
_USER = "00000000-0000-0000-0000-000000000001"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_hae_workouts_v2_and_v1() -> None:
    payload = _load("health_auto_export_workouts_redacted.json")
    rows = apple_health_workouts.normalize_apple_health_cardio_from_payload(payload, _USER)
    assert len(rows) == 2
    run = next(r for r in rows if r["activity_type"] == "Outdoor Run")
    assert run["source"] == "apple_health"
    assert run["source_id"] == "apple_health:workout-redacted-uuid-0001"
    assert run["event_date"].isoformat() == "2024-06-01"
    assert run["duration_min"] == pytest.approx(35.0)
    assert run["distance_miles"] == pytest.approx(5.2)
    assert run["avg_hr"] == 152
    assert run["max_hr"] == 178
    assert run["calories"] == 520
    assert run["elevation_ft"] is not None

    ride = next(r for r in rows if "Ride" in r["activity_type"])
    assert ride["source_id"].startswith("apple_health:h:")
    assert ride["duration_min"] == pytest.approx(75.0)
    assert ride["distance_miles"] == pytest.approx(25.0 * apple_health_workouts.KM_TO_MILES, rel=1e-3)


def test_started_at_parsed_and_source_app_defaults_none() -> None:
    payload = _load("health_auto_export_workouts_redacted.json")
    rows = apple_health_workouts.normalize_apple_health_cardio_from_payload(payload, _USER)
    run = next(r for r in rows if r["activity_type"] == "Outdoor Run")
    # Fixture omits per-workout ``source`` → source_app is None (neutral rank in dedup).
    assert run["source_app"] is None
    assert run["started_at"] is not None
    assert run["started_at"].isoformat() == "2024-06-01T07:30:00+00:00"


def test_source_app_captured_from_workout() -> None:
    payload = {
        "data": {
            "metrics": [],
            "workouts": [
                {
                    "id": "nrc-1",
                    "name": "Outdoor Run",
                    "source": "Nike Run Club",
                    "start": "2026-07-02 06:30:00 +0000",
                    "end": "2026-07-02 06:56:54 +0000",
                    "duration": 1614,
                    "distance": {"qty": 3.1, "units": "mi"},
                },
                {
                    "name": "Outdoor Run",
                    "source": {"name": "Strava"},
                    "start": "2026-07-02 06:30:05 +0000",
                    "end": "2026-07-02 06:57:00 +0000",
                },
            ],
        }
    }
    rows = apple_health_workouts.normalize_apple_health_cardio_from_payload(payload, _USER)
    assert rows[0]["source_app"] == "Nike Run Club"
    assert rows[1]["source_app"] == "Strava"  # object {name: ...} form


def test_source_app_from_nested_sample_provenance_chains() -> None:
    """HAE API export nests provenance as ``Device|App|App`` on per-sample sources.

    The run's samples chain through Health Sync/NRC/Strava: because Health Sync
    (the Fitbit/Google → HealthKit bridge) is in the chain, the run was *bridged*,
    so NRC/Strava are only mirrors riding along the provenance — the row resolves
    to Health Sync (Fitbit tier), not NRC. The walk likewise only sees Health Sync.
    """
    payload = _load("health_auto_export_workouts_api_source_chains.json")
    rows = apple_health_workouts.normalize_apple_health_cardio_from_payload(payload, _USER)
    run = next(r for r in rows if r["activity_type"] == "Outdoor Run")
    walk = next(r for r in rows if r["activity_type"] == "Outdoor Walk")
    assert run["source_app"] == "Health Sync"
    assert walk["source_app"] == "Health Sync"


def test_ingest_payload_complete_includes_cardio() -> None:
    payload = _load("health_auto_export_workouts_redacted.json")
    payload["data"]["metrics"] = [
        {
            "name": "step_count",
            "units": "count",
            "data": [{"qty": 9000, "date": "2024-06-01 22:00:00 +0000"}],
        }
    ]
    captured: list[tuple[str, bytes]] = []

    def raw_put(key: str, body: bytes) -> None:
        captured.append((key, body))

    from datetime import datetime, timezone

    k, bio, cardio = apple_health_export.ingest_apple_health_payload_complete(
        _USER, payload, raw_put=raw_put, utc_now=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    )
    assert len(captured) == 1
    assert k == captured[0][0]
    assert len(bio) == 1
    assert bio[0]["metric"] == "steps"
    assert len(cardio) == 2

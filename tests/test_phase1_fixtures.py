"""Phase 1: validate redacted vendor payloads under tests/fixtures/ match expected shapes."""

from __future__ import annotations

import json
from pathlib import Path

# Mirrors canonical names documented in schema/soma-planned-schema.sql (biometrics).
_CANONICAL_BIOMETRIC_METRICS: frozenset[str] = frozenset(
    {
        "hrv_rmssd",
        "resting_hr",
        "sleep_hours",
        "sleep_deep_hrs",
        "sleep_rem_hrs",
        "sleep_score",
        "steps",
        "active_cal",
        "vo2_max",
        "body_weight_lbs",
        "body_fat_pct",
        "muscle_mass_lbs",
        "spo2_pct",
        "respiratory_rate",
    }
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(rel: str) -> object:
    path = _FIXTURES / rel
    return json.loads(path.read_text(encoding="utf-8"))


def test_hevy_list_workouts_fixture_matches_paginated_shape() -> None:
    raw = _load_json("hevy/get_workouts_page1_redacted.json")
    assert isinstance(raw, dict)
    assert raw.get("page") == 1
    assert isinstance(raw.get("page_count"), int)
    workouts = raw.get("workouts")
    assert isinstance(workouts, list) and len(workouts) >= 1
    w0 = workouts[0]
    assert isinstance(w0, dict)
    assert isinstance(w0.get("id"), str) and len(w0["id"]) > 0
    assert isinstance(w0.get("start_time"), str)
    exercises = w0.get("exercises")
    assert isinstance(exercises, list) and len(exercises) >= 1
    ex0 = exercises[0]
    assert isinstance(ex0.get("title"), str) and len(ex0["title"]) > 0
    assert "superset_id" in ex0
    sets = ex0.get("sets")
    assert isinstance(sets, list) and len(sets) >= 1
    s0 = sets[0]
    assert "index" in s0
    assert "type" in s0


def test_biometrics_daily_fixture_uses_canonical_metric_names() -> None:
    raw = _load_json("biometrics/health_export_daily_redacted.json")
    assert isinstance(raw, dict)
    assert raw.get("source") == "apple_health_export"
    assert isinstance(raw.get("event_date"), str)
    metrics = raw.get("metrics")
    assert isinstance(metrics, list) and len(metrics) >= 1
    for row in metrics:
        assert isinstance(row, dict)
        metric = row.get("metric")
        assert metric in _CANONICAL_BIOMETRIC_METRICS
        assert isinstance(row.get("value"), int | float)

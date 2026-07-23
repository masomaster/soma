"""Tests for cardio training-load filters (NEAT walks + query-time dedup)."""

from __future__ import annotations

from datetime import date

from pipeline.cardio_training_load import (
    counts_toward_cardio_training_load,
    filter_cardio_for_training_load,
    is_bridge_neat_walk,
)
from pipeline.workload_pace import build_workload_pace_summary


def test_fitbit_walk_excluded_from_training_load():
    walk = {
        "event_date": "2026-07-20",
        "activity_type": "Walking",
        "source": "apple_health",
        "source_app": "Health Sync",
        "duration_min": 45.0,
    }
    run = {
        "event_date": "2026-07-20",
        "activity_type": "Outdoor Run",
        "source": "apple_health",
        "source_app": "Nike Run Club",
        "duration_min": 40.0,
        "started_at": "2026-07-20T07:00:00Z",
        "source_id": "nrc-1",
    }
    assert is_bridge_neat_walk(walk)
    assert not counts_toward_cardio_training_load(walk)
    assert counts_toward_cardio_training_load(run)

    kept = filter_cardio_for_training_load([walk, run])
    assert len(kept) == 1
    assert kept[0]["duration_min"] == 40.0


def test_same_day_sessions_without_start_not_collapsed():
    """Duration-only fallback must not drop two real same-day rides."""
    a = {
        "event_date": "2026-07-18",
        "activity_type": "Ride",
        "source": "wahoo_fit",
        "duration_min": 45.0,
        "source_id": "ride-am",
    }
    b = {
        "event_date": "2026-07-18",
        "activity_type": "Ride",
        "source": "wahoo_fit",
        "duration_min": 48.0,
        "source_id": "ride-pm",
    }
    kept = filter_cardio_for_training_load([a, b])
    assert len(kept) == 2


def test_apple_hub_near_duplicate_runs_collapsed():
    nrc = {
        "event_date": "2026-07-18",
        "activity_type": "Outdoor Run",
        "source": "apple_health",
        "source_app": "Nike Run Club",
        "duration_min": 50.0,
        "started_at": "2026-07-18T06:30:00Z",
        "source_id": "nrc-a",
        "distance_miles": 5.0,
    }
    fitbit = {
        "event_date": "2026-07-18",
        "activity_type": "Running",
        "source": "apple_health",
        "source_app": "Fitbit",
        "duration_min": 55.0,
        "started_at": "2026-07-18T06:32:00Z",
        "source_id": "fit-a",
        "distance_miles": 4.2,
    }
    kept = filter_cardio_for_training_load([nrc, fitbit])
    assert len(kept) == 1
    assert kept[0]["source_id"] == "nrc-a"


def test_cross_source_start_aligned_dupes_collapsed():
    wahoo = {
        "event_date": "2026-07-18",
        "activity_type": "Ride",
        "source": "wahoo_fit",
        "duration_min": 60.0,
        "started_at": "2026-07-18T08:00:00Z",
        "source_id": "wahoo-1",
        "avg_watts": 200.0,
    }
    apple = {
        "event_date": "2026-07-18",
        "activity_type": "Outdoor Cycle",
        "source": "apple_health",
        "source_app": "Strava",
        "duration_min": 62.0,
        "started_at": "2026-07-18T08:05:00Z",
        "source_id": "apple-1",
    }
    kept = filter_cardio_for_training_load([wahoo, apple])
    assert len(kept) == 1
    assert kept[0]["source_id"] == "wahoo-1"


def test_workload_pace_ignores_fitbit_walk_inflation():
    as_of = date(2026, 7, 20)
    cardio = [
        {
            "event_date": "2026-07-19",
            "activity_type": "Walking",
            "source": "apple_health",
            "source_app": "Fitbit",
            "duration_min": 300.0,
            "source_id": "walk-1",
        },
        {
            "event_date": "2026-07-19",
            "activity_type": "Ride",
            "source": "wahoo_fit",
            "duration_min": 60.0,
            "source_id": "ride-1",
            "started_at": "2026-07-19T08:00:00Z",
        },
    ]
    summary = build_workload_pace_summary(
        strength_events=[],
        cardio_events=cardio,
        as_of=as_of,
    )
    assert summary["cardio"]["acute_load"] == 60.0

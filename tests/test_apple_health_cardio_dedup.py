"""Source-aware near-duplicate Apple Health hub ``cardio_events`` deduplication."""

from __future__ import annotations

from datetime import date, datetime, timezone

from pipeline.apple_health_cardio_dedup import (
    activity_family,
    apple_cardio_rows_to_drop,
    filter_near_duplicate_apple_cardio,
    near_duplicate_apple_cardio,
)

_USER = "00000000-0000-0000-0000-000000000001"


def _row(
    *,
    source_id: str,
    event_date: date,
    activity_type: str = "Outdoor Run",
    duration_min: float = 30.0,
    distance_miles: float | None = 3.0,
    avg_hr: int | None = None,
    source_app: str | None = None,
    started_at: datetime | None = None,
) -> dict:
    return {
        "user_id": "u",
        "source": "apple_health",
        "source_app": source_app,
        "source_id": source_id,
        "event_date": event_date,
        "started_at": started_at,
        "activity_type": activity_type,
        "duration_min": duration_min,
        "distance_miles": distance_miles,
        "elevation_ft": None,
        "avg_hr": avg_hr,
        "max_hr": None,
        "avg_pace_sec_mi": None,
        "calories": None,
        "effort_zone": None,
        "session_rpe": None,
        "notes": None,
    }


class _FakeCursor:
    """Mimics psycopg2 rows for ``_load_existing_apple_cardio`` (16 columns) + DELETE."""

    def __init__(self, existing: list[dict]) -> None:
        self._existing = existing
        self.deleted_source_ids: list[str] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._params = params
        if sql.strip().upper().startswith("DELETE"):
            ids = params[1] if params else []
            self.deleted_source_ids = list(ids)
            self.rowcount = len(self.deleted_source_ids)

    def fetchall(self) -> list[tuple]:
        rows = []
        for r in self._existing:
            rows.append(
                (
                    r["source"],
                    r["source_id"],
                    r["event_date"],
                    r["activity_type"],
                    r["duration_min"],
                    r["distance_miles"],
                    r["elevation_ft"],
                    r["avg_hr"],
                    r["max_hr"],
                    r["avg_pace_sec_mi"],
                    r["calories"],
                    r["effort_zone"],
                    r["session_rpe"],
                    r["notes"],
                    r["started_at"],
                    r["source_app"],
                )
            )
        return rows


def test_activity_family_maps_variants() -> None:
    assert activity_family("Outdoor Run") == "run"
    assert activity_family("Running") == "run"
    assert activity_family("Outdoor Walk") == "walk"
    assert activity_family("Cycling") == "ride"
    assert activity_family("Traditional Strength Training") == "strength"


def test_near_duplicate_by_start_time_ignores_bad_duration() -> None:
    """Fitbit's wildly-off duration still matches when start times align."""
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    nrc = _row(source_id="apple_health:nrc", event_date=d, duration_min=26.9,
               source_app="Nike Run Club", started_at=t)
    fitbit = _row(source_id="apple_health:fit", event_date=d, duration_min=36.6,
                  distance_miles=3.4, source_app="Health Sync",
                  started_at=t.replace(minute=33))
    assert near_duplicate_apple_cardio(nrc, fitbit)


def test_not_duplicate_when_starts_far_apart() -> None:
    d = date(2026, 7, 2)
    morning = _row(source_id="a", event_date=d,
                   started_at=datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc))
    evening = _row(source_id="b", event_date=d,
                   started_at=datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc))
    assert not near_duplicate_apple_cardio(morning, evening)


def test_not_duplicate_different_family() -> None:
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    run = _row(source_id="1", event_date=d, activity_type="Outdoor Run", started_at=t)
    ride = _row(source_id="2", event_date=d, activity_type="Cycling", started_at=t)
    assert not near_duplicate_apple_cardio(run, ride)


def test_legacy_fallback_without_start_time() -> None:
    """Pre-0006 rows (no started_at) fall back to the duration tolerance."""
    d = date(2024, 6, 1)
    a = _row(source_id="apple_health:uuid-a", event_date=d)
    b = _row(source_id="apple_health:uuid-b", event_date=d, duration_min=32.0)
    assert near_duplicate_apple_cardio(a, b)


def test_batch_keeps_nike_over_strava_and_fitbit() -> None:
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    nrc = _row(source_id="apple_health:nrc", event_date=d, duration_min=26.9,
               source_app="Nike Run Club", started_at=t)
    strava = _row(source_id="apple_health:strava", event_date=d, duration_min=26.9,
                  distance_miles=3.3, avg_hr=150, source_app="Strava", started_at=t)
    fitbit = _row(source_id="apple_health:fit", event_date=d, duration_min=36.6,
                  source_app="Health Sync", started_at=t.replace(minute=31))
    cur = _FakeCursor([])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[strava, fitbit, nrc]
    )
    assert dropped == 2
    assert [r["source_id"] for r in kept] == ["apple_health:nrc"]
    assert superseded == []


def test_fitbit_only_walk_is_kept() -> None:
    """Fitbit walk with no higher-priority overlap survives."""
    d = date(2026, 6, 28)
    t = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    fitbit_walk = _row(source_id="apple_health:fit-walk", event_date=d,
                       activity_type="Walk", duration_min=29.9,
                       source_app="Health Sync", started_at=t)
    cur = _FakeCursor([])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[fitbit_walk]
    )
    assert dropped == 0
    assert len(kept) == 1
    assert superseded == []


def test_incoming_nike_supersedes_stored_fitbit() -> None:
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    stored_fitbit = _row(source_id="apple_health:fit", event_date=d, duration_min=36.6,
                         source_app="Health Sync", started_at=t.replace(minute=32))
    incoming_nrc = _row(source_id="apple_health:nrc", event_date=d, duration_min=26.9,
                        source_app="Nike Run Club", started_at=t)
    cur = _FakeCursor([stored_fitbit])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[incoming_nrc]
    )
    assert [r["source_id"] for r in kept] == ["apple_health:nrc"]
    assert dropped == 0
    assert superseded == ["apple_health:fit"]


def test_incoming_fitbit_dropped_when_stored_nike_present() -> None:
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    stored_nrc = _row(source_id="apple_health:nrc", event_date=d, duration_min=26.9,
                     source_app="Nike Run Club", started_at=t)
    incoming_fitbit = _row(source_id="apple_health:fit", event_date=d, duration_min=36.6,
                          source_app="Health Sync", started_at=t.replace(minute=34))
    cur = _FakeCursor([stored_nrc])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[incoming_fitbit]
    )
    assert kept == []
    assert dropped == 1
    assert superseded == []


def test_no_supersede_when_disabled_keeps_stored_row() -> None:
    """Unauthenticated path must not delete stored rows: incoming is dropped instead."""
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    stored_fitbit = _row(source_id="apple_health:fit", event_date=d, duration_min=36.6,
                         source_app="Health Sync", started_at=t.replace(minute=32))
    incoming_nrc = _row(source_id="apple_health:nrc", event_date=d, duration_min=26.9,
                        source_app="Nike Run Club", started_at=t)
    cur = _FakeCursor([stored_fitbit])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[incoming_nrc], allow_supersede=False
    )
    assert kept == []
    assert dropped == 1
    assert superseded == []


def test_glitched_copy_loses_to_clean_same_app_despite_higher_id() -> None:
    """Regression: a corrupt-distance run must not win a cluster on the UUID tiebreak.

    All three copies are Nike Run Club (same source rank) with equal field richness,
    so the pre-fix tiebreak fell through to ``str(source_id)`` descending and kept the
    glitched 0.86 mi partial (``…A42DE…`` sorts above ``…799E…``/``…285B…``). Distance
    trust now outranks the UUID tiebreak, so a real 2.625 mi copy wins.
    """
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 17, 9, tzinfo=timezone.utc)
    glitched = _row(source_id="apple_health:A42DE", event_date=d, duration_min=35.83,
                    distance_miles=0.8642, source_app="Nike Run Club", started_at=t)
    clean_a = _row(source_id="apple_health:799E", event_date=d, duration_min=27.6,
                   distance_miles=2.625, source_app="Nike Run Club", started_at=t)
    clean_b = _row(source_id="apple_health:285B", event_date=d, duration_min=26.9,
                   distance_miles=2.625, source_app="Nike Run Club", started_at=t)
    cur = _FakeCursor([])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[glitched, clean_a, clean_b]
    )
    assert dropped == 2
    assert len(kept) == 1
    assert kept[0]["distance_miles"] == 2.625
    assert kept[0]["source_id"] != "apple_health:A42DE"
    assert superseded == []


def test_clean_copy_supersedes_stored_glitched_same_app() -> None:
    """A clean re-ingest heals a stored glitched row of the same session/app."""
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 17, 9, tzinfo=timezone.utc)
    stored_glitched = _row(source_id="apple_health:A42DE", event_date=d, duration_min=35.83,
                           distance_miles=0.8642, source_app="Nike Run Club", started_at=t)
    incoming_clean = _row(source_id="apple_health:799E", event_date=d, duration_min=27.6,
                          distance_miles=2.625, source_app="Nike Run Club", started_at=t)
    cur = _FakeCursor([stored_glitched])
    kept, dropped, superseded = filter_near_duplicate_apple_cardio(
        cur, user_id=_USER, cardio_rows=[incoming_clean]
    )
    assert [r["source_id"] for r in kept] == ["apple_health:799E"]
    assert dropped == 0
    assert superseded == ["apple_health:A42DE"]


def test_rows_to_drop_prefers_priority() -> None:
    d = date(2026, 7, 2)
    t = datetime(2026, 7, 2, 6, 30, tzinfo=timezone.utc)
    nrc = _row(source_id="apple_health:nrc", event_date=d, source_app="Nike Run Club",
               started_at=t)
    fitbit = _row(source_id="apple_health:fit", event_date=d, distance_miles=3.4,
                  avg_hr=150, source_app="Health Sync", started_at=t.replace(minute=30, second=30))
    drops = apple_cardio_rows_to_drop([nrc, fitbit])
    assert [r["source_id"] for r in drops] == ["apple_health:fit"]

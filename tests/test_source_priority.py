"""Cardio source-app priority ranking (drives cross-app dedup resolution)."""

from __future__ import annotations

from pipeline.source_priority import (
    DEFAULT_CARDIO_SOURCE_APP_RANK,
    cardio_source_app_rank,
)


def test_priority_order_nrc_over_strava_over_watch_over_fitbit() -> None:
    nrc = cardio_source_app_rank("Nike Run Club")
    strava = cardio_source_app_rank("Strava")
    watch = cardio_source_app_rank("Mason's Apple Watch")
    fitbit = cardio_source_app_rank("Health Sync")
    assert nrc > strava > watch > fitbit


def test_health_sync_beats_generic_health_via_longest_match() -> None:
    # "Health Sync" (Fitbit/Google bridge) must not inherit the generic "Health" tier.
    assert cardio_source_app_rank("Health Sync") < cardio_source_app_rank("Health")


def test_unknown_and_empty_fall_back_to_default() -> None:
    assert cardio_source_app_rank("Some New App") == DEFAULT_CARDIO_SOURCE_APP_RANK
    assert cardio_source_app_rank(None) == DEFAULT_CARDIO_SOURCE_APP_RANK
    assert cardio_source_app_rank("") == DEFAULT_CARDIO_SOURCE_APP_RANK


def test_fitbit_and_google_rank_below_default() -> None:
    assert cardio_source_app_rank("Fitbit") < DEFAULT_CARDIO_SOURCE_APP_RANK
    assert cardio_source_app_rank("Google Fit") < DEFAULT_CARDIO_SOURCE_APP_RANK

"""Cardio source-app priority ranking (drives cross-app dedup resolution)."""

from __future__ import annotations

from pipeline.source_priority import (
    DEFAULT_CARDIO_SOURCE_APP_RANK,
    best_cardio_source_app,
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


def test_best_source_app_bridged_chain_is_fitbit_not_mirrored_origin() -> None:
    """Regression: when a run's only NRC/Strava mention is inside a Health Sync
    chain, it was bridged (Fitbit/Google), so it must resolve to the bridge — not
    be mislabeled NRC on a mirror token riding along the chain."""
    chains = ["SuperPhone|Health Sync|Nike Run Club|Strava", "Bluetooth Device|Health Sync"]
    assert best_cardio_source_app(chains) == "Health Sync"


def test_best_source_app_native_nrc_wins_even_when_a_bridge_chain_exists() -> None:
    """Not every workout is bridged: NRC writes straight to Apple Health, so an
    NRC chain with no bridge marks a native recorder that wins over bridged chains."""
    chains = ["SuperPhone|Nike Run Club", "Bluetooth Device|Health Sync"]
    assert best_cardio_source_app(chains) == "Nike Run Club"


def test_best_source_app_trusts_native_app_when_no_bridge() -> None:
    assert best_cardio_source_app(["SuperPhone|Nike Run Club"]) == "Nike Run Club"
    assert best_cardio_source_app(["Strava", "Mason's Apple Watch"]) == "Strava"


def test_best_source_app_none_when_only_devices() -> None:
    assert best_cardio_source_app(["SuperPhone|Bluetooth Device"]) is None

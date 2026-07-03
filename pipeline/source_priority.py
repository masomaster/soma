"""Source dedup priority when the same session appears from multiple integrations.

**Apple Health hub:** Renpho body comp, Google Fit / Fitbit sleep and activities
(Health Sync → Apple Health), Watch metrics, and Strava/NRC-mirrored workouts all
land via the **same** Apple Health webhook, so they share ``source = 'apple_health'``.
The originating app is preserved per row in ``cardio_events.source_app`` (the
HealthKit source name). Near-duplicate ``cardio_events`` are resolved in
:func:`pipeline.apple_health_cardio_dedup.filter_near_duplicate_apple_cardio` using
:data:`CARDIO_SOURCE_APP_PRIORITY`; Hevy strength dupes in
:func:`pipeline.apple_hevy_cardio_dedup.filter_apple_strength_cardio_when_hevy_present`.

**Strength:** Hevy is canonical for logged sets.

**Cardio priority (highest wins):** Nike Run Club > Strava > native Apple Watch /
iPhone > Fitbit / Google (Health Sync). Rationale: the same run is often logged by
NRC, mirrored to Strava, *and* copied from Fitbit via Health Sync — NRC is the
trusted primary, and Fitbit's duration/distance is unreliable, so it loses whenever
it overlaps another source (but is kept when it is the only record of a session).
"""

from __future__ import annotations

from collections.abc import Iterable

# App-name (HealthKit ``source_app``) priority for apple_health cardio dedup.
# Keys are matched as normalized substrings (see :func:`cardio_source_app_rank`),
# so device names like "Mason's Apple Watch" match the "apple watch" key.
CARDIO_SOURCE_APP_PRIORITY: dict[str, int] = {
    "nike run club": 40,
    "nike+ run club": 40,
    "nrc": 40,
    "strava": 30,
    "apple watch": 20,
    "iphone": 18,
    "health": 16,  # generic "Health"/"Health app" writers
    "health sync": 10,  # Google Fit / Fitbit bridge — least accurate
    "fitbit": 10,
    "google fit": 10,
    "google": 10,
}

# Unknown apps rank above Fitbit/Google but below native Apple sources, so an
# unrecognized source is never silently dropped in favor of Fitbit.
DEFAULT_CARDIO_SOURCE_APP_RANK = 15

# Bridge apps copy third-party data (Fitbit / Google Fit) into Apple HealthKit.
# When a workout's provenance chain passes through one of these, the origin-app
# names that ride along the chain (e.g. "Nike Run Club", "Strava") did **not**
# record it natively — the bridge is the real, less-reliable writer. Attributing
# such a row to a mirror app would wrongly inflate its dedup priority and mislabel
# its source, so bridge presence takes precedence in :func:`best_cardio_source_app`.
BRIDGE_SOURCE_APPS: tuple[str, ...] = ("health sync", "fitbit", "google fit", "google")

STRENGTH_SOURCE_PRIORITY: dict[str, int] = {
    "hevy": 30,
    "manual": 20,
}


def _normalize_source_app(source_app: object) -> str:
    if not isinstance(source_app, str):
        return ""
    return " ".join(source_app.strip().lower().split())


def cardio_source_app_rank(source_app: object) -> int:
    """Rank an Apple Health ``source_app`` name (higher wins in dedup).

    Matches known apps by normalized substring so HealthKit device names
    (e.g. ``"Mason's Apple Watch"``) resolve to the ``"apple watch"`` tier.
    Unknown / empty names fall back to :data:`DEFAULT_CARDIO_SOURCE_APP_RANK`.
    """
    normalized = _normalize_source_app(source_app)
    if not normalized:
        return DEFAULT_CARDIO_SOURCE_APP_RANK
    # Prefer the most specific (longest) matching keyword so "health sync" beats
    # the generic "health" tier rather than max-ing to the wrong rank.
    best_keyword_len = -1
    best_rank = DEFAULT_CARDIO_SOURCE_APP_RANK
    for keyword, rank in CARDIO_SOURCE_APP_PRIORITY.items():
        if keyword in normalized and len(keyword) > best_keyword_len:
            best_keyword_len = len(keyword)
            best_rank = rank
    return best_rank


def _is_bridge_app(name: str) -> bool:
    """True when a source name is a Fitbit/Google → HealthKit bridge (see :data:`BRIDGE_SOURCE_APPS`)."""
    normalized = _normalize_source_app(name)
    return any(bridge in normalized for bridge in BRIDGE_SOURCE_APPS)


def best_cardio_source_app(chains: Iterable[object]) -> str | None:
    """Pick the source app that actually recorded a workout from its provenance chains.

    HAE's API export records workout provenance as pipe-delimited **chains** on the
    nested per-sample ``source`` fields (e.g. ``"SuperPhone|Health Sync|Nike Run
    Club"``), not a single top-level app. Pass the chain strings here (do **not**
    pre-split them — the chain grouping is what decides native vs bridged).

    Data reaches Apple Health two ways (see module docstring):

    * **Native** — an app writes straight to HealthKit (e.g. Nike Run Club, Apple
      Watch). Its provenance chain contains **no** bridge app.
    * **Bridged** — Health Sync copies Fitbit/Google data in. Any chain containing
      a :data:`BRIDGE_SOURCE_APPS` token is bridged; origin-app names that ride
      along that chain ("Nike Run Club"/"Strava") are mirrors, not the recorder.

    Resolution: the highest-priority app that appears in a **non-bridged** chain
    wins (a genuine native recorder). If no native app is present, the workout was
    bridged, so the bridge app is returned (keeping it at the Fitbit tier instead
    of mislabeling it — and inflating its dedup priority — as NRC). Tokens at
    :data:`DEFAULT_CARDIO_SOURCE_APP_RANK` (device names like ``"SuperPhone"``,
    unknown apps) never win. Returns ``None`` when no chain names a known app.
    """
    native_name: str | None = None
    native_rank = -1
    bridge_name: str | None = None
    for chain in chains:
        if not isinstance(chain, str):
            continue
        tokens = [tok.strip() for tok in chain.split("|") if tok.strip()]
        chain_bridged = any(_is_bridge_app(tok) for tok in tokens)
        for tok in tokens:
            if _is_bridge_app(tok):
                if bridge_name is None:
                    bridge_name = tok[:200]
                continue
            if chain_bridged:
                continue
            rank = cardio_source_app_rank(tok)
            if rank == DEFAULT_CARDIO_SOURCE_APP_RANK:
                continue
            if rank > native_rank:
                native_rank = rank
                native_name = tok[:200]
    return native_name if native_name is not None else bridge_name

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


def best_cardio_source_app(candidates: Iterable[object]) -> str | None:
    """Pick the highest-priority *recognized* app among candidate source names.

    HAE's API export records workout provenance as pipe-delimited chains on the
    nested per-sample ``source`` fields (e.g. ``"SuperPhone|Health Sync|Nike Run
    Club"``), not a single top-level app. Callers split those chains into tokens
    and pass them here. Tokens that rank at :data:`DEFAULT_CARDIO_SOURCE_APP_RANK`
    (device names like ``"SuperPhone"`` and unknown apps) are skipped so a phone
    name never outranks a real app; ties keep the first seen. Returns the winning
    app string, or ``None`` when no token maps to a known app.
    """
    best_name: str | None = None
    best_rank = -1
    for cand in candidates:
        if not isinstance(cand, str):
            continue
        name = cand.strip()
        if not name:
            continue
        rank = cardio_source_app_rank(name)
        if rank == DEFAULT_CARDIO_SOURCE_APP_RANK:
            continue
        if rank > best_rank:
            best_rank = rank
            best_name = name[:200]
    return best_name

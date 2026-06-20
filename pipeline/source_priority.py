"""Source dedup priority when the same session appears from multiple integrations.

**Apple Health hub:** Renpho body comp, Google Fit / Fitbit sleep and activities
(Health Sync → Apple Health), Watch metrics, and Strava/NRC-mirrored workouts all
land via the **same** Apple Health webhook. Near-duplicate ``cardio_events`` are
filtered in :func:`pipeline.apple_health_cardio_dedup.filter_near_duplicate_apple_cardio`;
Hevy strength dupes in :func:`pipeline.apple_hevy_cardio_dedup.filter_apple_strength_cardio_when_hevy_present`.

**Strength:** Hevy is canonical for logged sets.

**Cardio (future Strava):** When Strava live ingest unpauses, ``CARDIO_SOURCE_PRIORITY``
ranks Strava above Apple Health for cross-source dedup.
"""

from __future__ import annotations

# Higher wins when resolving duplicate cardio sessions (future cross-source dedup).
CARDIO_SOURCE_PRIORITY: dict[str, int] = {
    "strava": 30,
    "apple_health": 10,
}

STRENGTH_SOURCE_PRIORITY: dict[str, int] = {
    "hevy": 30,
    "manual": 20,
}

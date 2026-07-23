"""Cross-source cardio priority for power-rich FIT imports.

Priority (highest wins): ``wahoo_fit`` > ``strava_export`` > ``apple_health`` /
legacy ``strava`` summary rows. Used when the same ride appears from Dropbox FIT
and an Apple Health Strava mirror (no watts).
"""

from __future__ import annotations

POWER_CARDIO_SOURCE_PRIORITY: dict[str, int] = {
    "wahoo_fit": 50,
    "strava_export": 40,
    "strava": 25,
    "apple_health": 20,
}

DEFAULT_POWER_CARDIO_SOURCE_RANK = 10


def power_cardio_source_rank(source: object) -> int:
    """Rank a ``cardio_events.source`` value for cross-source power dedup."""
    if not isinstance(source, str):
        return DEFAULT_POWER_CARDIO_SOURCE_RANK
    key = source.strip().lower()
    return POWER_CARDIO_SOURCE_PRIORITY.get(key, DEFAULT_POWER_CARDIO_SOURCE_RANK)

"""Distance unit conversions — the single source of truth for km ↔ miles.

Soma presents distance to the athlete in **statute miles** everywhere (matching
the imperial convention already used for ``body_weight_lbs``, ``distance_miles``
on ``cardio_events``, ``avg_pace_sec_mi``, and ``elevation_ft``). A few internal
rollups still store a base metric unit (``running_sessions.distance_km``,
``weekly_activity_summary.running_km``, and the ``mileage_check`` JSON keys), so
every user-facing surface converts through these helpers instead of scattering
magic constants like ``* 1.60934`` / ``* 0.621371`` across the codebase.
"""

from __future__ import annotations

# One statute mile is exactly 1609.344 m ⇒ 1.609344 km (matches
# ``METERS_PER_MILE = 1609.344`` used by the cardio adapters).
KM_PER_MILE = 1.609344
MILES_PER_KM = 1.0 / KM_PER_MILE  # ≈ 0.6213711922


def km_to_miles(km: float | int | None) -> float | None:
    """Convert kilometers to statute miles (``None`` passes through)."""
    if km is None:
        return None
    return float(km) * MILES_PER_KM


def miles_to_km(miles: float | int | None) -> float | None:
    """Convert statute miles to kilometers (``None`` passes through)."""
    if miles is None:
        return None
    return float(miles) * KM_PER_MILE

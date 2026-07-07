"""SSM-overridable defaults for workload-pace RYG indicators."""

from __future__ import annotations

# Evidence-backed defaults (see docs/plans/workload-indicators.md).
DEFAULT_PACE_THRESHOLDS: dict[str, float] = {
    "pace_acwr_green_low": 0.8,
    "pace_acwr_green_high": 1.3,
    "pace_acwr_yellow_high": 1.5,
    "pace_acwr_yellow_low": 0.6,
    "pace_wow_spike_yellow_strength_pct": 12.0,
    "pace_wow_spike_red_strength_pct": 20.0,
    "pace_wow_spike_yellow_cardio_pct": 10.0,
    "pace_wow_spike_red_cardio_pct": 15.0,
    "pace_wow_drop_yellow_pct": 25.0,
    "pace_wow_drop_red_pct": 40.0,
    "pace_vs_month_yellow_pct": 30.0,
    "pace_vs_month_red_pct": 50.0,
}

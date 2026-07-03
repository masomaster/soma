"""Cross-table metric_patterns correlation tests (sleep vs cardio/strength).

These exercise the deterministic, offline correlation engine that lets the
coaching chat answer cross-metric questions without the LLM running its own
statistics or a SQL join (see ``.cursor/rules/soma.mdc``).
"""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.metric_patterns import (
    compute_all_metric_patterns,
    compute_cross_metric_patterns,
)

_USER = "u1"
AS_OF = date(2024, 6, 30)


def _metrics_history(days: int = 40) -> list[dict]:
    """daily_health_metrics-shaped rows with a varying sleep series."""
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = AS_OF - timedelta(days=i)
        sleep = 6.0 + (i % 5) * 0.3
        rows.append({"metric_date": d, "sleep_hours": sleep, "hrv_rmssd": 40.0 + sleep})
    return rows


def _features_history(days: int = 40, *, linear: bool = True) -> list[dict]:
    """daily_features-shaped rows whose training outcomes track that day's sleep.

    With ``linear=False`` the feature values are flat (no variance), so no
    correlation can be computed — used to assert the engine emits no false
    pattern rather than manufacturing one.
    """
    rows: list[dict] = []
    for i in range(1, days + 1):
        d = AS_OF - timedelta(days=i)
        sleep = 6.0 + (i % 5) * 0.3
        rows.append(
            {
                "feature_date": d,
                "strength_tonnage_7d": (2.0 + sleep * 1.5) if linear else 5.0,
                "cardio_minutes_7d": (20.0 + sleep * 4.0) if linear else 30.0,
                "overall_readiness_score": (50.0 + sleep * 2.0) if linear else 60.0,
            }
        )
    return rows


def test_cross_metric_series_assembly_finds_known_correlation():
    """A perfect linear sleep->strength relation is detected at lag 0 (r≈1)."""
    patterns = compute_cross_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(),
        daily_features_history=_features_history(),
    )
    assert patterns
    pair = next(
        (
            p
            for p in patterns
            if p["metric_a"] == "sleep_hours"
            and p["metric_b"] == "strength_tonnage_7d"
            and p["lag_days"] == 0
        ),
        None,
    )
    assert pair is not None
    assert pair["correlation"] > 0.99
    assert pair["sample_n"] >= 14
    assert pair["status"] == "active"


def test_cross_metric_detects_sleep_cardio_and_stores_shape():
    """Sleep↔cardio is detected and rows use the metric_patterns column set."""
    patterns = compute_cross_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(),
        daily_features_history=_features_history(),
    )
    cardio = next(
        (p for p in patterns if p["metric_b"] == "cardio_minutes_7d"),
        None,
    )
    assert cardio is not None
    assert cardio["correlation"] > 0.45
    assert set(cardio) == {
        "user_id",
        "metric_a",
        "metric_b",
        "lag_days",
        "correlation",
        "sample_n",
        "status",
        "description",
    }
    assert "sleep hours" in cardio["description"]


def test_compute_all_merges_within_and_cross_patterns():
    all_patterns = compute_all_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(),
        daily_features_history=_features_history(),
    )
    metric_bs = {p["metric_b"] for p in all_patterns}
    # within-table (hrv_rmssd) and cross-table (strength/cardio) both present
    assert "hrv_rmssd" in metric_bs
    assert "strength_tonnage_7d" in metric_bs
    # no duplicate (metric_a, metric_b, lag) keys survive the merge
    keys = [(p["metric_a"], p["metric_b"], p["lag_days"]) for p in all_patterns]
    assert len(keys) == len(set(keys))


def test_compute_all_without_features_is_within_table_only():
    all_patterns = compute_all_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(),
    )
    assert all_patterns
    assert all(p["metric_b"] in {"hrv_rmssd", "resting_hr", "active_cal"} for p in all_patterns)


def test_insufficient_samples_report_no_pattern():
    """Below MIN_SAMPLE_N overlapping days, no (false) pattern is emitted."""
    patterns = compute_cross_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(days=10),
        daily_features_history=_features_history(days=10),
    )
    assert patterns == []


def test_no_pattern_for_flat_series():
    """Enough samples but flat (zero-variance) outcomes → no false pattern."""
    patterns = compute_cross_metric_patterns(
        user_id=_USER,
        as_of=AS_OF,
        daily_metrics_history=_metrics_history(days=40),
        daily_features_history=_features_history(days=40, linear=False),
    )
    assert patterns == []

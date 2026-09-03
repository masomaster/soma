"""Unit tests for ingest-time ``daily_features`` recompute (readiness path)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from pipeline.daily_features_recompute import (
    feature_dates_affected_by_metric_days,
    recompute_daily_features,
)


_USER = "00000000-0000-0000-0000-000000000001"


def test_feature_dates_affected_by_metric_days_spans_acute_window() -> None:
    assert feature_dates_affected_by_metric_days(
        [date(2026, 8, 27)], through=date(2026, 9, 3)
    ) == [
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 29),
        date(2026, 8, 30),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
    ]


def test_recompute_daily_features_writes_readiness_when_sleep_present() -> None:
    cur = MagicMock()
    metrics = [
        {
            "metric_date": date(2026, 8, 21) + __import__("datetime").timedelta(days=i),
            "sleep_hours": 7.0,
            "hrv_rmssd": 50.0,
        }
        for i in range(7)
    ]
    # Three SELECT fetches: metrics, strength, cardio
    fetch_queue = [metrics, [], []]

    def fetchall() -> list:
        return fetch_queue.pop(0) if fetch_queue else []

    cur.fetchall.side_effect = fetchall
    cur.description = [
        ("metric_date",),
        ("sleep_hours",),
        ("hrv_rmssd",),
    ]

    with patch("pipeline.daily_features_recompute.upsert_row") as upsert:
        # Override description per call by returning dicts via patched loaders
        with (
            patch(
                "pipeline.daily_features_recompute._load_metrics_window",
                return_value=metrics,
            ),
            patch(
                "pipeline.daily_features_recompute._load_strength_window",
                return_value=[],
            ),
            patch(
                "pipeline.daily_features_recompute._load_cardio_window",
                return_value=[],
            ),
        ):
            out = recompute_daily_features(
                cur, user_id=_USER, dates=[date(2026, 8, 27)]
            )

    assert len(out) == 1
    assert out[0]["feature_date"] == date(2026, 8, 27)
    assert out[0]["recovery_sleep_days_7d"] == 7
    assert out[0]["overall_readiness_score"] is not None
    upsert.assert_called_once()
    assert upsert.call_args.args[1] == "daily_features"

"""Tests for daily body-weight trend windows."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.weight_trend import (
    build_weight_trend,
    format_weight_glance,
    rolling_means,
    weight_goal_from_text,
    weight_points,
)


def _row(d: date, lbs: float) -> dict:
    return {"metric_date": d, "body_weight_lbs": lbs}


def test_weight_points_keep_latest_reading_per_day_and_ignore_the_future():
    as_of = date(2026, 10, 10)
    points = weight_points(
        [
            _row(as_of, 165.0),
            _row(as_of, 164.4),
            _row(as_of + timedelta(days=1), 190.0),
            {"metric_date": as_of - timedelta(days=1), "body_weight_lbs": None},
        ],
        as_of=as_of,
    )
    assert points == [(as_of, 164.4)]


def test_sparse_history_does_not_invent_a_seven_day_average():
    as_of = date(2026, 10, 10)
    trend = build_weight_trend(
        [_row(date(2026, 9, 3), 165.6)],
        as_of=as_of,
    )
    assert trend["latest_lbs"] == 165.6
    assert trend["latest_date"] == "2026-09-03"
    assert trend["days_since_weigh_in"] == 37
    assert trend["avg_7d_lbs"] is None
    assert trend["vs_4wk_lbs"] is None
    lines = dict(format_weight_glance(trend))
    assert "37d ago" in lines["Body weight"]
    assert lines["Weight vs 4-week avg"] == (
        "Not enough weigh-ins in the 4 weeks before the last reading"
    )


def test_vs_four_week_average_uses_calendar_days():
    as_of = date(2026, 10, 10)
    # Last 7 days average 164. Recent-but-older readings in the 28-day window pull
    # the 4-week mean up, so the latest week is below that average.
    history = [
        _row(as_of - timedelta(days=20), 170.0),
        _row(as_of - timedelta(days=10), 168.0),
        _row(as_of - timedelta(days=2), 164.0),
        _row(as_of, 164.0),
    ]
    trend = build_weight_trend(history, as_of=as_of)
    assert trend["readings_7d"] == 2
    assert trend["avg_7d_lbs"] == 164.0
    assert trend["readings_28d"] == 4
    assert trend["avg_28d_lbs"] == 166.5
    assert trend["vs_4wk_lbs"] == -2.5


def test_change_over_four_weeks_compares_separated_windows():
    as_of = date(2026, 10, 10)
    history = [
        _row(as_of - timedelta(days=30), 170.0),
        _row(as_of - timedelta(days=1), 165.0),
        _row(as_of, 165.0),
    ]
    trend = build_weight_trend(history, as_of=as_of)
    assert trend["change_4w_lbs"] == -5.0


def test_cut_progress_uses_phase_baseline_and_parsed_goal():
    as_of = date(2026, 10, 20)
    phase_start = date(2026, 10, 10)
    history = [
        _row(date(2026, 10, 9), 166.0),
        _row(as_of, 164.2),
    ]
    loss, goal_date = weight_goal_from_text("Lose about 5 lb by 2026-12-01, from the first weigh-in.")
    assert loss == 5.0
    assert goal_date == date(2026, 12, 1)
    trend = build_weight_trend(
        history,
        as_of=as_of,
        phase_start=phase_start,
        target_loss_lbs=loss,
        target_date=goal_date,
    )
    assert trend["phase_baseline_lbs"] == 166.0
    assert trend["change_since_phase_lbs"] == -1.8
    assert trend["remaining_loss_lbs"] == 3.2
    text = dict(format_weight_glance(trend))["Cut progress"]
    assert "−5 lb" in text
    assert "3.2 lb to go" in text
    assert "2026-12-01" in text


def test_goal_without_an_in_phase_weigh_in_asks_for_one():
    trend = build_weight_trend(
        [{"metric_date": date(2026, 9, 3), "body_weight_lbs": 165.6}],
        as_of=date(2026, 10, 10),
        phase_start=date(2026, 10, 10),
        target_loss_lbs=5.0,
        target_date=date(2026, 12, 1),
    )
    assert trend["change_since_phase_lbs"] is None
    lines = dict(format_weight_glance(trend))
    assert "waiting on a weigh-in" in lines["Cut goal"]


def test_rolling_means_do_not_cross_calendar_gaps():
    points = [
        (date(2026, 6, 1), 160.0),
        (date(2026, 10, 1), 170.0),
    ]
    means = rolling_means(points, window_days=7)
    assert means[date(2026, 10, 1)] == 170.0
    assert means[date(2026, 6, 1)] == 160.0


def test_trend_follows_the_last_weigh_in_when_today_has_none():
    as_of = date(2026, 10, 10)
    last = date(2026, 9, 3)
    history = [
        _row(last - timedelta(days=20), 160.0),
        _row(last - timedelta(days=10), 162.0),
        _row(last - timedelta(days=2), 164.0),
        _row(last, 165.0),
    ]
    trend = build_weight_trend(history, as_of=as_of)
    assert trend["days_since_weigh_in"] == 37
    assert trend["readings_28d"] == 4
    assert trend["avg_7d_lbs"] == 164.5
    assert trend["vs_4wk_lbs"] == 1.8
    assert trend["change_since_first_lbs"] == 5.0
    assert trend["first_date"] == "2026-08-14"
    text = dict(format_weight_glance(trend))
    assert "+5.0 lb since 2026-08-14" in text["Weight since first reading"]


def test_weight_goal_from_text_ignores_unstructured_notes():
    assert weight_goal_from_text("Lift once a week.") == (None, None)
    assert weight_goal_from_text(None) == (None, None)

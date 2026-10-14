"""Daily weight log: gap rows, 7-day prior average, weekly change."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.weight_tracking import daily_weight_log, weight_trend_line


def _row(day: date, pounds: float, fat: float | None = None) -> dict:
    item: dict = {"metric_date": day, "body_weight_lbs": pounds}
    if fat is not None:
        item["body_fat_pct"] = fat
    return item


def test_january_sheet_weekly_change_is_avg_minus_avg_seven_days_earlier():
    """January 10's weekly change is that day's 7-day average minus January 3's."""
    year = 2026
    weights = {
        date(year, 1, 1): 165.2,
        date(year, 1, 4): 163.2,
        date(year, 1, 5): 164.8,
        date(year, 1, 6): 164.6,
        date(year, 1, 7): 164.8,
        date(year, 1, 9): 163.6,
        date(year, 1, 11): 163.6,
        date(year, 1, 12): 163.2,
        date(year, 1, 13): 162.2,
        date(year, 1, 14): 161.8,
        date(year, 1, 16): 164.0,
    }
    log = daily_weight_log([_row(day, pounds) for day, pounds in weights.items()])
    by_day = {row["metric_date"]: row for row in log}

    assert log[0]["metric_date"] == date(year, 1, 1)
    assert log[-1]["metric_date"] == date(year, 1, 16)
    assert len(log) == 16
    assert by_day[date(year, 1, 2)]["body_weight_lbs"] is None
    assert by_day[date(year, 1, 3)]["avg_7d_lbs"] == 165.2
    assert by_day[date(year, 1, 10)]["body_weight_lbs"] is None
    assert by_day[date(year, 1, 10)]["avg_7d_lbs"] == 164.2
    assert by_day[date(year, 1, 10)]["weekly_change_lbs"] == -1.0
    assert by_day[date(year, 1, 10)]["weekly_change_lbs"] == (
        by_day[date(year, 1, 10)]["avg_7d_lbs"] - by_day[date(year, 1, 3)]["avg_7d_lbs"]
    )
    assert by_day[date(year, 1, 7)]["weekly_change_lbs"] is None


def test_average_uses_only_days_with_a_weigh_in():
    start = date(2026, 6, 18)
    history = [
        _row(start, 159.2, fat=20.5),
        _row(start + timedelta(days=2), 161.0, fat=20.9),
    ]
    log = daily_weight_log(history)
    assert [row["metric_date"] for row in log] == [
        start,
        start + timedelta(days=1),
        start + timedelta(days=2),
    ]
    gap = log[1]
    assert gap["body_weight_lbs"] is None
    assert gap["body_fat_pct"] is None
    assert gap["avg_7d_lbs"] == 159.2
    assert log[2]["avg_7d_lbs"] == 160.1
    assert log[2]["body_fat_pct"] == 20.9


def test_blank_body_fat_stays_blank():
    day = date(2026, 7, 12)
    log = daily_weight_log([{"metric_date": day, "body_weight_lbs": 164.0, "body_fat_pct": "--"}])
    assert log[0]["body_weight_lbs"] == 164.0
    assert log[0]["body_fat_pct"] is None


def test_later_same_day_reading_wins():
    day = date(2026, 7, 20)
    log = daily_weight_log(
        [
            _row(day, 163.0),
            _row(day, 164.0, fat=21.7),
        ]
    )
    assert len(log) == 1
    assert log[0]["body_weight_lbs"] == 164.0
    assert log[0]["body_fat_pct"] == 21.7


def test_trend_line_falls_when_weight_falls():
    start = date(2026, 6, 1)
    points = [(start + timedelta(days=i), 170.0 - i) for i in range(10)]
    line = weight_trend_line(points)
    assert len(line) == 2
    assert line[0][0] == start
    assert line[1][0] == start + timedelta(days=9)
    assert line[1][1] < line[0][1]

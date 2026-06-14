"""Phase 6 persistence tests (allow-listed sparse upserts) with a fake cursor."""

from __future__ import annotations

import json
from datetime import date

import pytest

from pipeline import persistence as P

RUN = date(2024, 6, 8)


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, statement, values=None):  # noqa: ANN001 - psycopg2 cursor shape
        self.calls.append((statement, values))


def test_upsert_daily_features_binds_all_columns():
    cur = FakeCursor()
    row = {"user_id": "u1", "feature_date": RUN, "strength_sessions_7d": 2, "sleep_debt_7d": 7.0}
    P.upsert_row(cur, "daily_features", row)
    assert len(cur.calls) == 1
    _stmt, values = cur.calls[0]
    assert list(values) == ["u1", RUN, 2, 7.0]


def test_upsert_daily_briefings_serializes_jsonb():
    cur = FakeCursor()
    row = {
        "user_id": "u1",
        "briefing_date": RUN,
        "coaching_note": "Easy day.",
        "flags": ["LOW_HRV"],
        "features_json": {"overall_readiness_score": 44.0},
    }
    P.upsert_row(cur, "daily_briefings", row)
    _stmt, values = cur.calls[0]
    # features_json must be serialized to a JSON string for the JSONB column.
    assert json.loads(values[-1]) == {"overall_readiness_score": 44.0}
    assert ["LOW_HRV"] in values  # TEXT[] passed through as a Python list


def test_upsert_rejects_unknown_column():
    cur = FakeCursor()
    with pytest.raises(KeyError, match="unknown column"):
        P.upsert_row(cur, "daily_features", {"user_id": "u1", "feature_date": RUN, "evil": 1})


def test_upsert_rejects_missing_conflict_key():
    cur = FakeCursor()
    with pytest.raises(KeyError, match="conflict key"):
        P.upsert_row(cur, "daily_features", {"user_id": "u1", "strength_sessions_7d": 1})


def test_upsert_rejects_unknown_table():
    cur = FakeCursor()
    with pytest.raises(KeyError, match="Unsupported table"):
        P.upsert_row(cur, "robots", {"user_id": "u1"})


def test_replace_statistical_anomaly_events_deletes_then_inserts():
    cur = FakeCursor()
    row = {
        "user_id": "u1",
        "detected_date": RUN,
        "metric": "hrv_rmssd",
        "anomaly_type": "statistical",
        "description": "hrv_rmssd z-score -2.5 vs prior baseline (mean 50.0, n=20)",
        "severity": "info",
        "context_json": {"z_score": -2.5, "method": "z_score"},
    }
    P.replace_statistical_anomaly_events(cur, user_id="u1", detected_date=RUN, rows=[row])
    assert len(cur.calls) == 2
    del_stmt, del_vals = cur.calls[0]
    assert "DELETE FROM anomaly_events" in str(del_stmt)
    assert del_vals == ("u1", RUN, "statistical")
    ins_stmt, ins_vals = cur.calls[1]
    assert "INSERT INTO anomaly_events" in str(ins_stmt)
    assert ins_vals[0] == "u1"
    assert ins_vals[3] == "statistical"
    assert json.loads(ins_vals[-1])["z_score"] == -2.5


def test_replace_statistical_anomaly_events_empty_rows_still_deletes():
    cur = FakeCursor()
    P.replace_statistical_anomaly_events(cur, user_id="u1", detected_date=RUN, rows=[])
    assert len(cur.calls) == 1
    assert "DELETE" in str(cur.calls[0][0])


def test_replace_statistical_anomaly_events_rejects_wrong_type():
    cur = FakeCursor()
    bad = {
        "user_id": "u1",
        "detected_date": RUN,
        "metric": "x",
        "anomaly_type": "llm_pattern",
        "description": "nope",
        "severity": "info",
        "context_json": {},
    }
    with pytest.raises(KeyError, match="statistical"):
        P.replace_statistical_anomaly_events(cur, user_id="u1", detected_date=RUN, rows=[bad])

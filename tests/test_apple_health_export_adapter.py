"""Phase 7: Apple Health export (HAE JSON + Soma envelope) → biometrics rows."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.adapters import apple_health_export
from pipeline.biometrics_upsert import upsert_biometrics

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_USER = "00000000-0000-0000-0000-000000000001"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / "biometrics" / name).read_text(encoding="utf-8"))


def test_normalize_soma_daily_envelope_fixture() -> None:
    payload = _load("health_export_daily_redacted.json")
    rows = apple_health_export.normalize_apple_health_export_payload(payload, user_id=_USER)
    assert len(rows) == 3
    metrics = {r["metric"]: r["value"] for r in rows}
    assert metrics["hrv_rmssd"] == pytest.approx(48.2)
    assert metrics["resting_hr"] == pytest.approx(56.0)
    assert metrics["sleep_hours"] == pytest.approx(7.25)
    assert all(r["source"] == "apple_health_export" for r in rows)
    assert all(r["event_date"].isoformat() == "2024-06-01" for r in rows)


def test_normalize_health_auto_export_steps_sum_and_sleep_seconds() -> None:
    payload = _load("health_auto_export_metrics_redacted.json")
    rows = apple_health_export.normalize_apple_health_export_payload(payload, user_id=_USER)
    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        by_day.setdefault(r["event_date"].isoformat(), {})[r["metric"]] = r["value"]
    assert by_day["2024-06-01"]["steps"] == pytest.approx(300.0)
    assert by_day["2024-06-01"]["active_cal"] == pytest.approx(400.0)
    assert by_day["2024-06-02"]["steps"] == pytest.approx(150.0)
    assert by_day["2024-06-02"]["sleep_hours"] == pytest.approx(8.0)


def test_sleep_hours_uses_max_when_sync_posts_duplicate_nights() -> None:
    body = {
        "data": {
            "metrics": [
                {
                    "name": "sleep_analysis",
                    "units": "hr",
                    "data": [
                        {"date": "2024-06-01", "totalSleep": 7.0},
                        {"date": "2024-06-01", "totalSleep": 7.5},
                    ],
                }
            ]
        }
    }
    rows = apple_health_export.normalize_apple_health_export_payload(body, user_id=_USER)
    sleep = [r for r in rows if r["metric"] == "sleep_hours"]
    assert len(sleep) == 1
    assert sleep[0]["value"] == pytest.approx(7.5)


def test_event_date_camel_case_envelope() -> None:
    body = {"eventDate": "2024-06-03", "metrics": [{"metric": "steps", "value": 1200, "unit": "count"}]}
    rows = apple_health_export.normalize_apple_health_export_payload(body, user_id=_USER)
    assert len(rows) == 1
    assert rows[0]["event_date"].isoformat() == "2024-06-03"
    assert rows[0]["metric"] == "steps"


def test_weight_body_mass_maps_to_body_weight_lbs() -> None:
    body = {
        "event_date": "2024-06-01",
        "metrics": [{"metric": "weight_body_mass", "value": 180, "unit": "lb"}],
    }
    rows = apple_health_export.normalize_apple_health_export_payload(body, user_id=_USER)
    assert len(rows) == 1
    assert rows[0]["metric"] == "body_weight_lbs"
    assert rows[0]["value"] == pytest.approx(180.0)


def test_hae_body_composition_from_renpho_sync() -> None:
    """Renpho → Apple Health → HAE: weight, fat %, lean mass."""
    body = {
        "data": {
            "metrics": [
                {
                    "name": "body_mass",
                    "units": "kg",
                    "data": [{"date": "2024-06-01", "qty": 80.5}],
                },
                {
                    "name": "body_fat_percentage",
                    "units": "%",
                    "data": [{"date": "2024-06-01", "qty": 18.2}],
                },
                {
                    "name": "lean_body_mass",
                    "units": "kg",
                    "data": [{"date": "2024-06-01", "qty": 65.9}],
                },
            ]
        }
    }
    rows = apple_health_export.normalize_apple_health_export_payload(body, user_id=_USER)
    by_metric = {r["metric"]: r["value"] for r in rows if r["event_date"].isoformat() == "2024-06-01"}
    assert by_metric["body_weight_lbs"] == pytest.approx(80.5 * 2.2046226218, rel=1e-4)
    assert by_metric["body_fat_pct"] == pytest.approx(18.2)
    assert by_metric["muscle_mass_lbs"] == pytest.approx(65.9 * 2.2046226218, rel=1e-4)


def test_rollup_daily_health_metrics_accepts_normalized_rows() -> None:
    from pipeline import features

    payload = _load("health_export_daily_redacted.json")
    rows = apple_health_export.normalize_apple_health_export_payload(payload, user_id=_USER)
    wide = features.rollup_daily_health_metrics(
        rows, user_id=_USER, metric_date=rows[0]["event_date"]
    )
    assert wide.get("hrv_rmssd") == pytest.approx(48.2)
    assert wide.get("sleep_hours") == pytest.approx(7.25)


def test_normalize_list_of_envelopes_merges() -> None:
    one = _load("health_export_daily_redacted.json")
    two = {**one, "event_date": "2024-06-02", "metrics": [{"metric": "steps", "value": 5000, "unit": "count"}]}
    rows = apple_health_export.normalize_apple_health_export_payload([one, two], user_id=_USER)
    dates = {r["event_date"].isoformat() for r in rows}
    assert dates == {"2024-06-01", "2024-06-02"}


def test_ingest_webhook_attaches_raw_key() -> None:
    payload = _load("health_export_daily_redacted.json")
    captured: list[tuple[str, bytes]] = []
    utc = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)

    def raw_put(key: str, body: bytes) -> None:
        captured.append((key, body))

    key, rows = apple_health_export.ingest_apple_health_export_webhook(
        _USER, payload, raw_put=raw_put, utc_now=utc
    )
    assert len(rows) == 3
    assert all(r["raw_s3_key"] == key for r in rows)
    assert len(captured) == 1
    assert captured[0][0] == key


def test_ingest_bytes_round_trip() -> None:
    payload = _load("health_export_daily_redacted.json")
    raw = json.dumps(payload).encode("utf-8")
    captured: list[bytes] = []

    def raw_put(_k: str, body: bytes) -> None:
        captured.append(body)

    _key, rows = apple_health_export.ingest_apple_health_export_bytes(
        _USER, raw, raw_put=raw_put, utc_now=datetime(2024, 6, 1, tzinfo=timezone.utc)
    )
    assert captured == [raw]
    assert len(rows) == 3


def test_ingest_bytes_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        apple_health_export.ingest_apple_health_export_bytes(
            _USER,
            b"{",
            raw_put=lambda _k, _b: None,
            utc_now=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )


def test_upsert_biometrics_uses_on_conflict_do_update() -> None:
    cur = MagicMock()
    rows = [
        {
            "user_id": _USER,
            "source": "apple_health_export",
            "event_date": date(2024, 6, 1),
            "metric": "steps",
            "value": 100.0,
            "unit": "count",
            "raw_s3_key": "raw/u/apple_health_export/2024-06-01/x.json",
        }
    ]
    with patch("pipeline.biometrics_upsert.execute_values") as ev:
        upsert_biometrics(cur, rows)
    ev.assert_called_once()
    _c, sql, values = ev.call_args[0]
    assert "INSERT INTO biometrics" in sql
    assert "ON CONFLICT (user_id, source, event_date, metric) DO UPDATE" in sql
    assert len(values) == 1
    assert values[0][0] == _USER
    assert values[0][3] == "steps"

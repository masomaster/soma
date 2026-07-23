"""Tests for FTP estimate helpers (no DB)."""

from __future__ import annotations

from pipeline.ftp_estimate import collect_mmp_maps, estimate_ftp_for_rides
from pipeline.power_math import COGGAN_20MIN_FACTOR, estimate_ftp_from_best_mmp


def test_estimate_ftp_for_rides_prefers_hour_power() -> None:
    rides = [
        {"power_mmp_json": {"300": 320.0, "1200": 290.0, "3600": 200.0}},
        {"power_mmp_json": {"300": 310.0, "1200": 300.0, "3600": 210.0}},
    ]
    est = estimate_ftp_for_rides(rides)
    assert est["ftp_method"] == "mmp_60"
    assert est["ftp_watts"] == 210.0


def test_estimate_ftp_for_rides_coggan_fallback() -> None:
    # No 30/60 anchors and not enough mid points for CP → Coggan 20-min.
    rides = [
        {"power_mmp_json": {"300": 320.0, "1200": 290.0}},
        {"power_mmp_json": {"300": 310.0, "1200": 300.0}},
    ]
    est = estimate_ftp_for_rides(rides)
    assert est["ftp_method"] == "coggan_20min"
    assert est["ftp_watts"] == round(300.0 * COGGAN_20MIN_FACTOR, 1)


def test_collect_mmp_from_json_string() -> None:
    maps = collect_mmp_maps([{"power_mmp_json": '{"1200": 250}'}])
    assert maps == [{"1200": 250.0}]


def test_estimate_insufficient() -> None:
    est = estimate_ftp_from_best_mmp({})
    assert est["ftp_method"] == "insufficient_data"

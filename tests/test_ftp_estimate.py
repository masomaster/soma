"""Tests for FTP estimate helpers (no DB)."""

from __future__ import annotations

from pipeline.ftp_estimate import collect_mmp_maps, estimate_ftp_for_rides
from pipeline.power_math import estimate_ftp_from_best_mmp


def test_estimate_ftp_for_rides_coggan() -> None:
    rides = [
        {"power_mmp_json": {"300": 320.0, "1200": 290.0}},
        {"power_mmp_json": {"300": 310.0, "1200": 300.0}},
    ]
    est = estimate_ftp_for_rides(rides)
    assert est["ftp_method"] == "coggan_20min"
    assert est["ftp_watts"] == round(300.0 * 0.95, 1)


def test_collect_mmp_from_json_string() -> None:
    maps = collect_mmp_maps([{"power_mmp_json": '{"1200": 250}'}])
    assert maps == [{"1200": 250.0}]


def test_estimate_insufficient() -> None:
    est = estimate_ftp_from_best_mmp({})
    assert est["ftp_method"] == "insufficient_data"

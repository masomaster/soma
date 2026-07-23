"""Hermetic tests for :mod:`pipeline.power_math`."""

from __future__ import annotations

from pipeline.power_math import (
    aggregate_best_mmp,
    avg_and_max_watts,
    coggan_ftp_from_mmp,
    critical_power_ftp,
    estimate_ftp_from_best_mmp,
    mean_maximal_power,
    normalized_power,
    work_kilojoules,
)


def test_mean_maximal_power_finds_best_window() -> None:
    # 10s @ 100W, then 5s @ 300W, then rest @ 100W
    watts = [100.0] * 10 + [300.0] * 5 + [100.0] * 10
    mmp = mean_maximal_power(watts, durations_sec=(5, 10))
    assert mmp["5"] == 300.0
    assert mmp["10"] == 200.0  # 5@300 + 5@100


def test_mean_maximal_power_skips_longer_than_series() -> None:
    mmp = mean_maximal_power([200.0] * 8, durations_sec=(5, 60))
    assert "5" in mmp
    assert "60" not in mmp


def test_avg_max_and_work() -> None:
    avg, mx = avg_and_max_watts([100.0, 200.0, None, 300.0])
    assert avg == 200.0
    assert mx == 300.0
    assert work_kilojoules([1000.0] * 2, sample_dt_sec=1.0) == 2.0


def test_normalized_power_requires_30s() -> None:
    assert normalized_power([200.0] * 10) is None
    np_val = normalized_power([200.0] * 60)
    assert np_val is not None
    assert 190.0 <= np_val <= 210.0


def test_coggan_ftp_from_20min() -> None:
    best = {"300": 300.0, "1200": 280.0}
    out = coggan_ftp_from_mmp(best)
    assert out is not None
    ftp, conf = out
    assert ftp == round(280.0 * 0.95, 1)
    assert 0.5 <= conf <= 0.95


def test_coggan_rejects_spike_only_curve() -> None:
    # 20-min far below 5-min → not a threshold effort
    assert coggan_ftp_from_mmp({"300": 400.0, "1200": 200.0}) is None


def test_critical_power_and_estimate_fallback() -> None:
    # Synthetic CP=250, W'=15000 → P(t)=250+15000/t
    best = {
        "180": 250 + 15000 / 180,
        "300": 250 + 15000 / 300,
        "720": 250 + 15000 / 720,
        "1200": 250 + 15000 / 1200,
    }
    # Force CP path: make 20-min fail Coggan gate vs 5-min
    best_spike = {"300": 500.0, "180": best["180"], "720": best["720"], "1200": 200.0}
    est = estimate_ftp_from_best_mmp(best_spike)
    assert est["ftp_method"] in {"critical_power", "insufficient_data"}
    cp = critical_power_ftp(best)
    assert cp is not None
    ftp, w_prime, conf = cp
    assert abs(ftp - 250.0) < 2.0
    assert w_prime > 0
    assert conf > 0


def test_insufficient_data() -> None:
    est = estimate_ftp_from_best_mmp({"5": 400.0})
    assert est["ftp_method"] == "insufficient_data"
    assert est["ftp_watts"] is None


def test_aggregate_best_mmp() -> None:
    best = aggregate_best_mmp([{"1200": 250}, {"1200": 260, "300": 300}])
    assert best["1200"] == 260.0
    assert best["300"] == 300.0

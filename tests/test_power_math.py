"""Hermetic tests for :mod:`pipeline.power_math`."""

from __future__ import annotations

from pipeline.power_math import (
    COGGAN_20MIN_FACTOR,
    CP_TO_FTP_FACTOR,
    MMP_30_TO_FTP_FACTOR,
    aggregate_best_mmp,
    avg_and_max_watts,
    coggan_ftp_from_mmp,
    critical_power_ftp,
    estimate_ftp_from_best_mmp,
    mean_maximal_power,
    monotone_mmp,
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
    assert ftp == round(280.0 * COGGAN_20MIN_FACTOR, 1)
    assert 0.45 <= conf <= 0.85


def test_coggan_rejects_spike_only_curve() -> None:
    # 20-min far below 5-min → not a threshold effort
    assert coggan_ftp_from_mmp({"300": 400.0, "1200": 200.0}) is None


def test_estimate_prefers_60min_when_hour_looks_maximal() -> None:
    # Strong 20-min peak but hour still near 30-min → trust hour.
    best = {
        "300": 300.0,
        "720": 270.0,
        "1200": 258.0,  # old Coggan 0.95 → ~245W
        "1800": 210.0,
        "3600": 190.0,
    }
    est = estimate_ftp_from_best_mmp(best)
    assert est["ftp_method"] == "mmp_60"
    assert est["ftp_watts"] == 190.0


def test_estimate_skips_soft_hour_on_ftp_test_day() -> None:
    # 2019-shaped FTP test: hard 20-min, soft hour from warmup/cooldown.
    # Soft 30-min vs 20-min → prefer outdoor Coggan over soft 30 / soft 60.
    best = {
        "300": 269.0,
        "720": 257.0,
        "1200": 251.8,
        "1800": 217.8,
        "3600": 140.6,
    }
    est = estimate_ftp_from_best_mmp(best)
    assert est["ftp_method"] == "coggan_20min"
    assert est["ftp_watts"] == round(251.8 * COGGAN_20MIN_FACTOR, 1)
    assert est["ftp_watts"] > 200.0


def test_estimate_skips_soft_hour_without_30min_anchor() -> None:
    # Soft hour with only a hard 20-min — do not trust ~141W as FTP.
    best = {
        "300": 269.0,
        "1200": 251.8,
        "3600": 140.6,
    }
    est = estimate_ftp_from_best_mmp(best)
    assert est["ftp_method"] == "coggan_20min"
    assert est["ftp_watts"] == round(251.8 * COGGAN_20MIN_FACTOR, 1)


def test_estimate_prefers_30min_over_inflated_coggan() -> None:
    # Strong 20-min above 30-min; m30 priority wins over outdoor Coggan.
    best = {
        "300": 300.0,
        "720": 270.0,
        "1200": 260.0,
        "1800": 200.0,
    }
    est = estimate_ftp_from_best_mmp(best)
    assert est["ftp_method"] == "mmp_30"
    assert est["ftp_watts"] == round(200.0 * MMP_30_TO_FTP_FACTOR, 1)


def test_estimate_uses_30min_when_no_hour() -> None:
    best = {"300": 280.0, "720": 250.0, "1200": 230.0, "1800": 210.0}
    est = estimate_ftp_from_best_mmp(best)
    assert est["ftp_method"] == "mmp_30"
    assert est["ftp_watts"] == round(210.0 * MMP_30_TO_FTP_FACTOR, 1)


def test_critical_power_scaled_to_ftp() -> None:
    # Synthetic CP=250, W'=15000 → P(t)=250+15000/t (no 30/60 anchors).
    best = {
        "300": 250 + 15000 / 300,
        "720": 250 + 15000 / 720,
        "1200": 250 + 15000 / 1200,
    }
    # Force CP path: Coggan gate fails (20-min << 5-min).
    best_spike = {
        "300": 500.0,
        "720": best["720"],
        "1200": 200.0,
    }
    est = estimate_ftp_from_best_mmp(best_spike)
    assert est["ftp_method"] == "critical_power"
    cp = critical_power_ftp(monotone_mmp(best_spike))
    assert cp is not None
    assert est["ftp_watts"] == round(cp[0] * CP_TO_FTP_FACTOR, 1)

    cp = critical_power_ftp(best)
    assert cp is not None
    ftp, w_prime, conf = cp
    assert abs(ftp - 250.0) < 2.0
    assert w_prime > 0
    assert conf > 0


def test_monotone_mmp_clamps_longer_windows() -> None:
    raw = {"180": 200.0, "300": 220.0, "1200": 180.0}
    mono = monotone_mmp(raw)
    assert mono["180"] == 200.0
    assert mono["300"] == 200.0  # clamped to shorter-duration ceiling
    assert mono["1200"] == 180.0


def test_monotone_mmp_skips_non_numeric_keys() -> None:
    mono = monotone_mmp({"300": 250.0, "metadata": 1.0, "1200": 200.0})
    assert "300" in mono
    assert "1200" in mono
    assert "metadata" not in mono


def test_insufficient_data() -> None:
    est = estimate_ftp_from_best_mmp({"5": 400.0})
    assert est["ftp_method"] == "insufficient_data"
    assert est["ftp_watts"] is None


def test_aggregate_best_mmp() -> None:
    best = aggregate_best_mmp([{"1200": 250}, {"1200": 260, "300": 300}])
    assert best["1200"] == 260.0
    assert best["300"] == 300.0


def test_aggregate_best_mmp_normalizes_duration_keys() -> None:
    best = aggregate_best_mmp([{"1200": 250}, {1200: 260}, {"1200.0": 255}])
    assert best == {"1200": 260.0}

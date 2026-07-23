"""Deterministic cycling power math: MMP, Normalized Power, FTP estimates.

No I/O. Used by FIT ingest and :mod:`pipeline.ftp_estimate`. The LLM never sees
raw watt streams — only pre-computed conclusions (``ftp_watts``, MMP summaries).

FTP estimation favors longer sustained efforts over short anaerobic peaks.
Outdoor incidental MMP is treated more conservatively than a formal test.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

# Durations (seconds) stored on each ride's ``power_mmp_json``.
MMP_DURATIONS_SEC: tuple[int, ...] = (5, 15, 30, 60, 180, 300, 720, 1200, 1800, 3600)

# Coggan classic lab protocol uses 0.95; outdoor *discovered* best-20s are not
# paced maximal tests, and group studies show 0.95 tends to overestimate hour power.
COGGAN_20MIN_FACTOR = 0.90
COGGAN_20MIN_SEC = 1200
# Best 20-min MMP should not be wildly below best 5-min (else not a threshold effort).
COGGAN_MIN_VS_5MIN_RATIO = 0.85
COGGAN_5MIN_SEC = 300

# Critical-power fit durations. Prefer mid/long efforts; exclude very short
# anaerobic points that inflate CP when MMP is a cross-ride Frankenstein curve.
CP_FIT_DURATIONS_SEC: tuple[int, ...] = (300, 720, 1200, 1800)
# CP sits above ~60-min power for most riders; scale before reporting as FTP.
CP_TO_FTP_FACTOR = 0.95

MMP_30MIN_SEC = 1800
MMP_60MIN_SEC = 3600
# 30-min mean is typically a bit above hour power.
MMP_30_TO_FTP_FACTOR = 0.95

DEFAULT_FTP_LOOKBACK_DAYS = 90


def mean_maximal_power(
    watts: Sequence[float | None],
    *,
    durations_sec: Sequence[int] = MMP_DURATIONS_SEC,
    sample_dt_sec: float = 1.0,
) -> dict[str, float]:
    """Return best mean power (watts) for each window length in ``durations_sec``.

    ``watts`` is a uniform timeline (missing samples as ``None`` treated as 0.0 for
    MMP, matching common device practice that includes coasting zeros). Keys in the
    result are stringified seconds for JSONB storage.
    """
    if sample_dt_sec <= 0:
        raise ValueError("sample_dt_sec must be positive")
    series = [0.0 if w is None else float(w) for w in watts]
    n = len(series)
    out: dict[str, float] = {}
    if n == 0:
        return out
    # Prefix sums for O(n) window means.
    prefix = [0.0] * (n + 1)
    for i, v in enumerate(series):
        prefix[i + 1] = prefix[i] + v
    for dur in durations_sec:
        win = max(1, int(round(dur / sample_dt_sec)))
        if win > n:
            continue
        best = None
        for start in range(0, n - win + 1):
            total = prefix[start + win] - prefix[start]
            mean = total / win
            if best is None or mean > best:
                best = mean
        if best is not None:
            out[str(int(dur))] = round(best, 2)
    return out


def normalized_power(
    watts: Sequence[float | None],
    *,
    sample_dt_sec: float = 1.0,
) -> float | None:
    """Coggan Normalized Power from a uniform power series.

    30-second rolling average, raise to 4th power, mean, then 4th root.
    Returns ``None`` when the series is shorter than 30 seconds of samples.
    """
    if sample_dt_sec <= 0:
        raise ValueError("sample_dt_sec must be positive")
    series = [0.0 if w is None else max(0.0, float(w)) for w in watts]
    win = max(1, int(round(30.0 / sample_dt_sec)))
    if len(series) < win:
        return None
    # Rolling mean via prefix sums.
    prefix = [0.0] * (len(series) + 1)
    for i, v in enumerate(series):
        prefix[i + 1] = prefix[i] + v
    rolling: list[float] = []
    for start in range(0, len(series) - win + 1):
        rolling.append((prefix[start + win] - prefix[start]) / win)
    if not rolling:
        return None
    fourth_mean = sum(r**4 for r in rolling) / len(rolling)
    if fourth_mean < 0:
        return None
    return round(fourth_mean**0.25, 2)


def work_kilojoules(
    watts: Sequence[float | None],
    *,
    sample_dt_sec: float = 1.0,
) -> float | None:
    """Mechanical work in kJ (sum watts × dt / 1000)."""
    if sample_dt_sec <= 0:
        raise ValueError("sample_dt_sec must be positive")
    total_j = 0.0
    any_power = False
    for w in watts:
        if w is None:
            continue
        any_power = True
        total_j += max(0.0, float(w)) * sample_dt_sec
    if not any_power:
        return None
    return round(total_j / 1000.0, 2)


def avg_and_max_watts(
    watts: Sequence[float | None],
) -> tuple[float | None, float | None]:
    """Mean and max over non-null samples (zeros included when present as 0.0)."""
    vals = [float(w) for w in watts if w is not None]
    if not vals:
        return None, None
    return round(sum(vals) / len(vals), 2), round(max(vals), 2)


def aggregate_best_mmp(
    ride_mmp_maps: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Pointwise max across per-ride MMP dicts (string duration keys → watts)."""
    best: dict[str, float] = {}
    for mmp in ride_mmp_maps:
        if not isinstance(mmp, Mapping):
            continue
        for key, raw in mmp.items():
            try:
                watts = float(raw)
            except (TypeError, ValueError):
                continue
            sk = str(key)
            prev = best.get(sk)
            if prev is None or watts > prev:
                best[sk] = round(watts, 2)
    return best


def _mmp_get(mmp: Mapping[str, float], duration_sec: int) -> float | None:
    raw = mmp.get(str(duration_sec))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def monotone_mmp(best_mmp: Mapping[str, float]) -> dict[str, float]:
    """Enforce non-increasing power as duration increases.

    Cross-ride pointwise-max curves can violate physiology (e.g. best 3-min from
    one ride above best 5-min from another). Clamping longer windows down keeps
    CP / Coggan gates from chasing impossible envelopes.
    """
    items = sorted(
        ((int(k), float(v)) for k, v in best_mmp.items() if _finite_positive(v)),
        key=lambda kv: kv[0],
    )
    out: dict[str, float] = {}
    floor: float | None = None
    for dur, watts in items:
        if floor is not None:
            watts = min(watts, floor)
        out[str(dur)] = round(watts, 2)
        floor = watts
    return out


def _finite_positive(raw: Any) -> bool:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def coggan_ftp_from_mmp(best_mmp: Mapping[str, float]) -> tuple[float, float] | None:
    """Return ``(ftp_watts, confidence)`` from best 20-min MMP when effort gates pass.

    Gate: 20-min MMP must exist and be ≥ ``COGGAN_MIN_VS_5MIN_RATIO`` × best 5-min
    when 5-min is available (avoids treating a short spike-only curve as threshold).

    Uses ``COGGAN_20MIN_FACTOR`` (0.90) — conservative vs the classic 0.95 lab protocol,
    because Soma feeds incidental outdoor MMP, not a paced 20-minute test.
    """
    m20 = _mmp_get(best_mmp, COGGAN_20MIN_SEC)
    if m20 is None or m20 <= 0:
        return None
    m5 = _mmp_get(best_mmp, COGGAN_5MIN_SEC)
    if m5 is not None and m5 > 0 and (m20 / m5) < COGGAN_MIN_VS_5MIN_RATIO:
        return None
    ftp = round(m20 * COGGAN_20MIN_FACTOR, 1)
    # Confidence rises when both 5- and 20-min exist and ratio looks threshold-like.
    if m5 is not None and m5 > 0:
        ratio = m20 / m5
        conf = 0.50 + 0.30 * min(1.0, max(0.0, (ratio - COGGAN_MIN_VS_5MIN_RATIO) / 0.1))
    else:
        conf = 0.45
    return ftp, round(min(0.85, conf), 3)


def critical_power_ftp(
    best_mmp: Mapping[str, float],
    *,
    durations_sec: Sequence[int] = CP_FIT_DURATIONS_SEC,
) -> tuple[float, float, float] | None:
    """2-parameter critical power: ``P(t) = CP + W'/t``.

    Linearize as ``P = CP + W' * (1/t)``. Needs ≥3 finite (duration, MMP) points.
    Returns ``(cp_watts, w_prime_j, confidence)`` or ``None``.

    Note: ``cp_watts`` is Critical Power, not FTP — callers should apply
    ``CP_TO_FTP_FACTOR`` (see :func:`estimate_ftp_from_best_mmp`).
    """
    points: list[tuple[float, float]] = []
    for dur in durations_sec:
        p = _mmp_get(best_mmp, int(dur))
        if p is None or p <= 0 or dur <= 0:
            continue
        points.append((1.0 / float(dur), float(p)))
    if len(points) < 3:
        return None
    # Ordinary least squares: P = a + b * (1/t) with a=CP, b=W'.
    n = float(len(points))
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xx = sum(x * x for x, _ in points)
    sum_xy = sum(x * y for x, y in points)
    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-12:
        return None
    b = (n * sum_xy - sum_x * sum_y) / denom
    a = (sum_y - b * sum_x) / n
    if a <= 0 or not math.isfinite(a):
        return None
    # W' can be slightly negative on noisy curves; still use CP if positive.
    w_prime = b  # joules when t in seconds and P in watts
    # Residual relative RMSE → confidence.
    ss_res = 0.0
    ss_tot = 0.0
    mean_y = sum_y / n
    for x, y in points:
        pred = a + b * x
        ss_res += (y - pred) ** 2
        ss_tot += (y - mean_y) ** 2
    if ss_tot <= 1e-9:
        r2 = 1.0
    else:
        r2 = max(0.0, 1.0 - ss_res / ss_tot)
    conf = round(0.35 + 0.45 * r2, 3)
    return round(a, 1), float(w_prime), min(0.85, conf)


def _clamp_ftp_to_long_mmp(ftp: float, best_mmp: Mapping[str, float]) -> float:
    """Keep modeled FTP from exceeding observed long sustained power."""
    capped = ftp
    m30 = _mmp_get(best_mmp, MMP_30MIN_SEC)
    if m30 is not None and m30 > 0:
        capped = min(capped, m30)
    m60 = _mmp_get(best_mmp, MMP_60MIN_SEC)
    if m60 is not None and m60 > 0:
        # Hour power is the FTP definition; allow 2% headroom for rounding/noise.
        capped = min(capped, m60 * 1.02)
    return round(capped, 1)


def estimate_ftp_from_best_mmp(
    best_mmp: Mapping[str, float],
) -> dict[str, Any]:
    """Estimate FTP, preferring longer sustained anchors over short peaks.

    Priority:
    1. ``mmp_60`` — best 60-min mean ≈ FTP by definition
    2. ``mmp_30`` — 0.95 × best 30-min mean
    3. ``critical_power`` — 0.95 × CP from mid-duration MMP
    4. ``coggan_20min`` — 0.90 × best 20-min (outdoor-conservative)

    Modeled estimates are clamped so they cannot exceed observed 30/60-min MMP.
    """
    curve = monotone_mmp(best_mmp)

    m60 = _mmp_get(curve, MMP_60MIN_SEC)
    if m60 is not None and m60 > 0:
        return {
            "ftp_watts": round(m60, 1),
            "ftp_method": "mmp_60",
            "ftp_confidence": 0.92,
            "supporting_mmp": dict(curve),
        }

    m30 = _mmp_get(curve, MMP_30MIN_SEC)
    if m30 is not None and m30 > 0:
        ftp = _clamp_ftp_to_long_mmp(m30 * MMP_30_TO_FTP_FACTOR, curve)
        return {
            "ftp_watts": ftp,
            "ftp_method": "mmp_30",
            "ftp_confidence": 0.80,
            "supporting_mmp": dict(curve),
        }

    cp = critical_power_ftp(curve)
    if cp is not None:
        cp_watts, _w, conf = cp
        ftp = _clamp_ftp_to_long_mmp(cp_watts * CP_TO_FTP_FACTOR, curve)
        return {
            "ftp_watts": ftp,
            "ftp_method": "critical_power",
            "ftp_confidence": conf,
            "supporting_mmp": dict(curve),
        }

    coggan = coggan_ftp_from_mmp(curve)
    if coggan is not None:
        ftp, conf = coggan
        ftp = _clamp_ftp_to_long_mmp(ftp, curve)
        return {
            "ftp_watts": ftp,
            "ftp_method": "coggan_20min",
            "ftp_confidence": conf,
            "supporting_mmp": dict(curve),
        }

    return {
        "ftp_watts": None,
        "ftp_method": "insufficient_data",
        "ftp_confidence": 0.0,
        "supporting_mmp": dict(curve),
    }

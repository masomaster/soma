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
            dur = _parse_duration_key(key)
            if dur is None:
                continue
            try:
                watts = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(watts):
                continue
            sk = str(dur)
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


def _parse_duration_key(key: Any) -> int | None:
    """Parse an MMP duration key to int seconds; return None if invalid."""
    try:
        dur = int(float(str(key)))
    except (TypeError, ValueError):
        return None
    return dur if dur > 0 else None


def monotone_mmp(best_mmp: Mapping[str, float]) -> dict[str, float]:
    """Enforce non-increasing power as duration increases.

    Cross-ride pointwise-max curves can violate physiology (e.g. best 3-min from
    one ride above best 5-min from another). Clamping longer windows down keeps
    CP / Coggan gates from chasing impossible envelopes.
    """
    parsed: list[tuple[int, float]] = []
    for key, raw in best_mmp.items():
        dur = _parse_duration_key(key)
        if dur is None or not _finite_positive(raw):
            continue
        parsed.append((dur, float(raw)))
    parsed.sort(key=lambda kv: kv[0])
    out: dict[str, float] = {}
    ceiling: float | None = None
    for dur, watts in parsed:
        if ceiling is not None:
            watts = min(watts, ceiling)
        out[str(dur)] = round(watts, 2)
        ceiling = watts
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
    w_prime = b  # joules when t in seconds and P in watts
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


# Hour MMP is trusted only when it is close to best 30-min (not a soft hour on
# an FTP-test day with a hard 20-min and easy warmup/cooldown).
_HOUR_PLAUSIBLE_VS_30_RATIO = 0.88
# When 30-min is missing, still reject hours that sit far below best 20-min
# (classic soft-hour FTP-test shape: hard 20, easy filler to 60). Kept looser
# than the 30-min gate because cross-ride best-20 can sit well above true FTP.
_HOUR_PLAUSIBLE_VS_20_RATIO = 0.70


def _hour_looks_maximal(
    m60: float,
    *,
    m30: float | None,
    m20: float | None = None,
) -> bool:
    """True when best 60-min power is consistent with mid-duration anchors.

    Soft hours (hard 20-min + easy warmup/cooldown) fail the 30-min and/or 20-min
    ratio checks so shorter sustained models can win. Without any mid-duration
    anchor, trust the hour over short-peak models.
    """
    if m30 is not None and m30 > 0 and m60 < m30 * _HOUR_PLAUSIBLE_VS_30_RATIO:
        return False
    if m20 is not None and m20 > 0 and m60 < m20 * _HOUR_PLAUSIBLE_VS_20_RATIO:
        return False
    return True


def _estimate_result(
    *,
    ftp_watts: float,
    ftp_method: str,
    ftp_confidence: float,
    curve: Mapping[str, float],
) -> dict[str, Any]:
    return {
        "ftp_watts": round(ftp_watts, 1),
        "ftp_method": ftp_method,
        "ftp_confidence": ftp_confidence,
        "supporting_mmp": dict(curve),
    }


def estimate_ftp_from_best_mmp(
    best_mmp: Mapping[str, float],
) -> dict[str, Any]:
    """Estimate FTP from sustained anchors, with soft-hour and spike guards.

    Priority:
    1. ``mmp_60`` — best 60-min mean when the hour looks maximal vs 30/20-min
    2. After a *soft hour* (or no hour): prefer outdoor Coggan 20-min when the
       30-min window also looks soft vs 20-min (FTP-test shape with cooldown),
       else ``mmp_30`` × 0.95 (caps incidental outdoor 20-min spikes)
    3. ``critical_power`` — 0.95 × CP from mid-duration MMP
    4. Remaining Coggan 20-min fallback

    Soft hours (FTP-test days with hard 20-min + easy filler) skip ``mmp_60``.
    """
    curve = monotone_mmp(best_mmp)
    m60 = _mmp_get(curve, MMP_60MIN_SEC)
    m30 = _mmp_get(curve, MMP_30MIN_SEC)
    m20 = _mmp_get(curve, COGGAN_20MIN_SEC)
    soft_hour = (
        m60 is not None
        and m60 > 0
        and not _hour_looks_maximal(m60, m30=m30, m20=m20)
    )

    if m60 is not None and m60 > 0 and not soft_hour:
        return _estimate_result(
            ftp_watts=m60,
            ftp_method="mmp_60",
            ftp_confidence=0.92,
            curve=curve,
        )

    coggan = coggan_ftp_from_mmp(curve)
    # Soft 30-min vs hard 20-min (common on FTP-test days that also soft-hour).
    soft_30 = (
        m20 is not None
        and m20 > 0
        and m30 is not None
        and m30 > 0
        and m30 < m20 * _HOUR_PLAUSIBLE_VS_30_RATIO
    )
    if soft_hour and soft_30 and coggan is not None:
        ftp, conf = coggan
        return _estimate_result(
            ftp_watts=ftp,
            ftp_method="coggan_20min",
            ftp_confidence=conf,
            curve=curve,
        )

    if m30 is not None and m30 > 0:
        return _estimate_result(
            ftp_watts=m30 * MMP_30_TO_FTP_FACTOR,
            ftp_method="mmp_30",
            ftp_confidence=0.80,
            curve=curve,
        )

    cp = critical_power_ftp(curve)
    if cp is not None:
        cp_watts, _w, conf = cp
        return _estimate_result(
            ftp_watts=cp_watts * CP_TO_FTP_FACTOR,
            ftp_method="critical_power",
            ftp_confidence=conf,
            curve=curve,
        )

    if coggan is not None:
        ftp, conf = coggan
        return _estimate_result(
            ftp_watts=ftp,
            ftp_method="coggan_20min",
            ftp_confidence=conf,
            curve=curve,
        )

    return {
        "ftp_watts": None,
        "ftp_method": "insufficient_data",
        "ftp_confidence": 0.0,
        "supporting_mmp": dict(curve),
    }

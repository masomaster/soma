"""Apple Health export path: Health Auto Export (and similar) JSON → ``biometrics``.

While **Strava is paused**, Strava and Nike Run Club runs that sync **into Apple
Health** can still reach Soma via this path (metrics rollups). **Per-run**
``cardio_events`` is not populated here yet—see ``docs/plans/apple-health-export.md``.

Typical flow (Phase 7): HTTP POST webhook → write **raw** JSON (S3 or disk) →
normalize to one row per ``(user_id, source, event_date, metric)`` for
:func:`pipeline.biometrics_upsert.upsert_biometrics`.

Supports:

1. **Health Auto Export** ``{"data": {"metrics": [...]}}`` — quantity samples are
   rolled up **per calendar day** (UTC date string prefix or parsed local
   timestamps). ``steps`` / ``active_cal`` **sum** intraday points; other
   canonical metrics use the **mean** of same-day samples.
2. **Soma daily envelope** (tests / manual backfill): ``{"event_date": "...",
   "metrics": [{"metric": "hrv_rmssd", "value": ...}, ...]}`` — already canonical.

Unknown vendor metric names are **ignored** (never written). ``hrv_rmssd`` may be
fed from HAE ``heart_rate_variability_sdnn`` — SDNN is not RMSSD; document as a
v0 proxy until a dedicated RMSSD export exists.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from typing import Any

from pipeline.features import DAILY_HEALTH_METRIC_COLUMNS, as_date
from pipeline.raw_storage import format_raw_object_key

logger = logging.getLogger(__name__)

APPLE_HEALTH_EXPORT_SOURCE = "apple_health_export"
KG_TO_LBS = 2.2046226218

# Metrics that aggregate by summing intraday samples (HealthKit daily semantics).
_SUM_PER_DAY: frozenset[str] = frozenset({"steps", "active_cal"})

# Normalized HAE ``name`` (see :func:`_normalize_vendor_metric_name`) → Soma canonical.
_HAE_NAME_TO_CANONICAL: dict[str, str] = {
    "active_energy": "active_cal",
    "active_energy_burned": "active_cal",
    "step_count": "steps",
    "resting_heart_rate": "resting_hr",
    "heart_rate_variability_sdnn": "hrv_rmssd",
    "heart_rate_variability_rmssd": "hrv_rmssd",
    "oxygen_saturation": "spo2_pct",
    "respiratory_rate": "respiratory_rate",
    "vo2_max": "vo2_max",
    "body_mass": "body_weight_lbs",
    "weight_body_mass": "body_weight_lbs",
    "body_fat_percentage": "body_fat_pct",
    "sleep_analysis": "sleep_hours",
    "sleepanalysis": "sleep_hours",
}


def _normalize_vendor_metric_name(raw: str) -> str:
    s = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if s.startswith("hkquantitytypeidentifier"):
        s = s[len("hkquantitytypeidentifier") :].lstrip("_")
    return s


def _parse_sample_date(raw: str) -> date | None:
    """Parse HAE-style timestamps to a calendar date (local/offset preserved in date only)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z").date()
    except ValueError:
        pass
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _qty_from_entry(entry: Mapping[str, Any]) -> float | None:
    q = entry.get("qty")
    if q is None and "Qty" in entry:
        q = entry.get("Qty")
    v = _num(q)
    if v is not None:
        return v
    return _num(entry.get("value"))


def _sleep_hours_from_aggregated_row(entry: Mapping[str, Any]) -> float | None:
    """Map HAE aggregated sleep row to ``sleep_hours`` (float hours)."""
    raw = entry.get("totalSleep")
    if raw is None:
        raw = entry.get("asleep")
    v = _num(raw)
    if v is None or v <= 0:
        return None
    # HealthKit often stores duration in seconds; sane sleep is < ~30h.
    if v > 72:
        return round(v / 3600.0, 4)
    return float(v)


def _canonical_for_hae_name(name: str) -> str | None:
    key = _normalize_vendor_metric_name(name)
    return _HAE_NAME_TO_CANONICAL.get(key)


def _body_weight_lbs_from_qty(qty: float, units: str) -> float | None:
    u = (units or "").strip().lower()
    if "kg" in u:
        return round(qty * KG_TO_LBS, 4)
    if "lb" in u:
        return float(qty)
    # Unknown unit — assume lb (US typical for Health app)
    logger.warning("body_mass without clear units %r; assuming pounds", units)
    return float(qty)


def _iter_hae_metric_samples(
    block: Mapping[str, Any],
) -> list[tuple[date, str, float, str | None]]:
    """Expand one HAE metrics[] block into (event_date, canonical, value, unit_hint)."""
    name_raw = block.get("name")
    if not isinstance(name_raw, str):
        return []
    canonical = _canonical_for_hae_name(name_raw)
    if canonical is None or canonical not in DAILY_HEALTH_METRIC_COLUMNS:
        return []

    units = block.get("units")
    units_s = units.strip() if isinstance(units, str) else None

    data = block.get("data")
    if not isinstance(data, list):
        return []

    out: list[tuple[date, str, float, str | None]] = []

    if canonical == "sleep_hours":
        for entry in data:
            if not isinstance(entry, dict):
                continue
            d_raw = entry.get("date") or entry.get("startDate") or entry.get("sleepStart")
            if not isinstance(d_raw, str):
                continue
            d = _parse_sample_date(d_raw)
            if d is None:
                continue
            hrs = _sleep_hours_from_aggregated_row(entry)
            if hrs is None:
                continue
            out.append((d, canonical, hrs, "h"))
        return out

    for entry in data:
        if not isinstance(entry, dict):
            continue
        d_raw = entry.get("date")
        if not isinstance(d_raw, str):
            continue
        d = _parse_sample_date(d_raw)
        if d is None:
            continue

        if canonical == "body_weight_lbs":
            q = _qty_from_entry(entry)
            if q is None:
                continue
            w = _body_weight_lbs_from_qty(q, units_s or "")
            v = w
        else:
            v = _qty_from_entry(entry)

        if v is None:
            continue
        out.append((d, canonical, float(v), units_s))
    return out


def _rollup_samples(
    samples: list[tuple[date, str, float, str | None]],
) -> list[tuple[date, str, float, str | None]]:
    """Merge same (day, metric) using sum vs mean rules."""
    # key -> {"sum": float, "n": int, "unit": str|None}
    buckets: dict[tuple[date, str], dict[str, Any]] = {}
    for d, metric, value, unit in samples:
        key = (d, metric)
        b = buckets.setdefault(key, {"sum": 0.0, "n": 0, "unit": unit})
        b["sum"] += value
        b["n"] += 1
        if unit and b.get("unit") is None:
            b["unit"] = unit

    merged: list[tuple[date, str, float, str | None]] = []
    for (d, metric), b in sorted(buckets.items(), key=lambda x: (x[0][0], x[0][1])):
        if metric in _SUM_PER_DAY:
            val = b["sum"]
        else:
            n = b["n"]
            val = b["sum"] / n if n else 0.0
        merged.append((d, metric, val, b.get("unit")))
    return merged


def normalize_health_auto_export_metrics(
    metrics: list[Any],
    *,
    user_id: str,
    source: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> list[dict[str, Any]]:
    """Normalize HAE ``data.metrics`` array to ``biometrics`` row dicts."""
    samples: list[tuple[date, str, float, str | None]] = []
    for block in metrics:
        if isinstance(block, dict):
            samples.extend(_iter_hae_metric_samples(block))
    rows: list[dict[str, Any]] = []
    for d, metric, value, unit in _rollup_samples(samples):
        rows.append(
            {
                "user_id": user_id,
                "source": source,
                "event_date": d,
                "metric": metric,
                "value": value,
                "unit": unit,
                "raw_s3_key": None,
            }
        )
    return rows


def normalize_soma_daily_envelope(
    envelope: Mapping[str, Any],
    *,
    user_id: str,
    default_source: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> list[dict[str, Any]]:
    """Normalize the repo's redacted daily rollup JSON to ``biometrics`` rows."""
    src = envelope.get("source")
    source = src if isinstance(src, str) and src.strip() else default_source
    d = as_date(envelope.get("event_date"))
    if d is None:
        return []
    metrics = envelope.get("metrics")
    if not isinstance(metrics, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        name_raw = item.get("metric")
        if not isinstance(name_raw, str):
            continue
        # Envelope ``metric`` may be canonical or HAE-style vendor keys (e.g. weight_body_mass).
        name = _canonical_for_hae_name(name_raw) or name_raw.strip()
        if name not in DAILY_HEALTH_METRIC_COLUMNS:
            continue
        v = _num(item.get("value"))
        if v is None:
            continue
        u = item.get("unit")
        unit = u.strip() if isinstance(u, str) else None
        rows.append(
            {
                "user_id": user_id,
                "source": source,
                "event_date": d,
                "metric": name,
                "value": v,
                "unit": unit,
                "raw_s3_key": None,
            }
        )
    return rows


def normalize_apple_health_export_payload(
    body: Any,
    *,
    user_id: str,
    default_source: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> list[dict[str, Any]]:
    """Dispatch on JSON root: HAE ``data.metrics``, Soma envelope, or list of dicts."""
    if isinstance(body, list):
        merged: list[dict[str, Any]] = []
        for item in body:
            merged.extend(
                normalize_apple_health_export_payload(
                    item, user_id=user_id, default_source=default_source
                )
            )
        return merged

    if not isinstance(body, dict):
        return []

    data = body.get("data")
    if isinstance(data, dict):
        m = data.get("metrics")
        if isinstance(m, list):
            return normalize_health_auto_export_metrics(
                m, user_id=user_id, source=default_source
            )

    mlist = body.get("metrics")
    if isinstance(mlist, list):
        ed = as_date(body.get("event_date")) or as_date(body.get("eventDate"))
        if ed is not None:
            env = {**body, "event_date": ed.isoformat()}
            return normalize_soma_daily_envelope(
                env, user_id=user_id, default_source=default_source
            )

    return []


def ingest_apple_health_export_webhook(
    user_id: str,
    body: Mapping[str, Any] | list[Any],
    *,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    raw_source_slug: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> tuple[str, list[dict[str, Any]]]:
    """Write raw JSON (UTF-8) then return ``(raw_key, biometrics rows)``.

    ``raw_source_slug`` becomes the middle segment of the raw key path
    (``raw/{user_id}/{slug}/...``) and defaults to ``apple_health_export``.
    """
    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    key = format_raw_object_key(user_id, raw_source_slug, at)
    payload_bytes = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
    raw_put(key, payload_bytes)
    logger.info("Recorded raw Apple Health export payload at key %s", key)
    rows = normalize_apple_health_export_payload(body, user_id=user_id)
    for r in rows:
        r["raw_s3_key"] = key
    return key, rows


def ingest_apple_health_export_bytes(
    user_id: str,
    raw_body: bytes,
    *,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    raw_source_slug: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate JSON, write **original** raw bytes once, return ``(raw_key, rows)``."""
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Apple Health export body was not valid UTF-8") from exc
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Apple Health export body was not valid JSON") from exc
    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    key = format_raw_object_key(user_id, raw_source_slug, at)
    raw_put(key, raw_body)
    logger.info("Recorded raw Apple Health export payload at key %s", key)
    rows = normalize_apple_health_export_payload(body, user_id=user_id)
    for r in rows:
        r["raw_s3_key"] = key
    return key, rows


def ingest_apple_health_payload_complete(
    user_id: str,
    body: Mapping[str, Any] | list[Any],
    *,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    raw_source_slug: str = APPLE_HEALTH_EXPORT_SOURCE,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Write raw JSON, normalize **biometrics** + **cardio_events** rows (HAE full export).

    ``cardio_events`` rows do not include ``raw_s3_key`` (column absent); the same
    raw object key applies to the whole POST.
    """
    from pipeline.adapters import apple_health_workouts

    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    key = format_raw_object_key(user_id, raw_source_slug, at)
    payload_bytes = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
    raw_put(key, payload_bytes)
    logger.info("Recorded raw Apple Health export payload at key %s", key)
    bio = normalize_apple_health_export_payload(body, user_id=user_id)
    cardio = apple_health_workouts.normalize_apple_health_cardio_from_payload(body, user_id)
    for r in bio:
        r["raw_s3_key"] = key
    return key, bio, cardio
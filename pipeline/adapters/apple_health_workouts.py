"""Health Auto Export (HAE) ``workouts`` → ``cardio_events`` rows (Apple Health hub).

Maps **HAE API Export** workout objects (v2 primary, v1 fallback) into the same
``cardio_events`` shape as Strava. ``source`` is ``apple_health``; ``source_id`` is
``apple_health:{uuid}`` when HAE provides ``id``, else a deterministic hash from
start time + normalized name.

See ``docs/plans/apple-health-export.md`` for payload references.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pipeline.source_priority import best_cardio_source_app
from pipeline.timeparse import parse_iso_datetime_utc

logger = logging.getLogger(__name__)

APPLE_HEALTH_CARDIO_SOURCE = "apple_health"
METERS_PER_MILE = 1609.344
METERS_TO_FEET = 3.280839895
KM_TO_MILES = 0.621371192
KJ_TO_KCAL = 0.239006


def _parse_workout_timestamp(raw: str) -> date | None:
    """Parse HAE workout ``start`` / ``end`` strings to a calendar date (aligned with metrics parser)."""
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


def _source_app(workout: Mapping[str, Any]) -> str | None:
    """Best-known HealthKit source app for the workout (e.g. "Nike Run Club").

    HAE's API export does **not** put a single app on the workout; instead each
    nested sample (``activeEnergy``, ``heartRate``, …) carries a pipe-delimited
    provenance chain such as ``"SuperPhone|Health Sync|Nike Run Club"``. We union
    those chains (plus any legacy top-level ``source`` string/``{name}`` object),
    split on ``|``, and let :func:`best_cardio_source_app` choose the highest
    priority recognized app. Returns ``None`` when no known app is present.
    """
    chains: set[str] = set()
    raw = workout.get("source")
    if isinstance(raw, str) and raw.strip():
        chains.add(raw)
    elif isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"].strip():
        chains.add(raw["name"])
    for value in workout.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                src = item.get("source")
                if isinstance(src, str) and src.strip():
                    chains.add(src)
    candidates = [token for chain in chains for token in chain.split("|")]
    return best_cardio_source_app(candidates)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _optional_int_hr(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _quantity_to_miles(obj: Any) -> float | None:
    """HAE ``{ qty, units }`` distance → statute miles."""
    if not isinstance(obj, dict):
        return None
    q = _num(obj.get("qty"))
    if q is None or q < 0:
        return None
    u = str(obj.get("units") or "").strip().lower()
    if "mi" in u:
        return round(q, 4)
    if "km" in u:
        return round(q * KM_TO_MILES, 4)
    if u in {"m", "meter", "meters"} or u.startswith("m "):
        return round(q / METERS_PER_MILE, 4)
    if "ft" in u or "feet" in u:
        return round(q / 5280.0, 4)
    logger.debug("Unknown distance units %r; assuming meters", u)
    return round(q / METERS_PER_MILE, 4)


def _quantity_to_feet(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None
    q = _num(obj.get("qty"))
    if q is None or q < 0:
        return None
    u = str(obj.get("units") or "").strip().lower()
    if "ft" in u or "feet" in u:
        return round(q, 2)
    if "mi" in u:
        return round(q * 5280.0, 2)
    if "km" in u:
        return round(q * 1000.0 * METERS_TO_FEET, 2)
    if "m" in u:
        return round(q * METERS_TO_FEET, 2)
    return round(q * METERS_TO_FEET, 2)


def _duration_minutes_from_seconds(sec: float | None) -> float | None:
    if sec is None or sec < 0:
        return None
    return round(float(sec) / 60.0, 4)


def _duration_sec_from_start_end(start: str, end: str) -> float | None:
    try:
        a = datetime.fromisoformat(start.strip().replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
        delta = (b - a).total_seconds()
        return float(delta) if delta >= 0 else None
    except (TypeError, ValueError):
        return None


def _nested_qty(obj: Any) -> float | None:
    """``avgHeartRate`` / ``maxHeartRate`` as ``{ qty, units }``."""
    if not isinstance(obj, dict):
        return None
    return _num(obj.get("qty"))


def _heart_rate_block_avg_max(w: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """v2 ``heartRate`` object with Min/Avg/Max (capital or lower)."""
    hr = w.get("heartRate")
    if isinstance(hr, dict):
        for ak, mk in (("Avg", "Max"), ("avg", "max")):
            av, mx = hr.get(ak), hr.get(mk)
            ah = _optional_int_hr(av.get("qty")) if isinstance(av, dict) else _optional_int_hr(av)
            mh = _optional_int_hr(mx.get("qty")) if isinstance(mx, dict) else _optional_int_hr(mx)
            if ah is not None or mh is not None:
                return ah, mh
    ah = _optional_int_hr(_nested_qty(w.get("avgHeartRate")))
    mh = _optional_int_hr(_nested_qty(w.get("maxHeartRate")))
    return ah, mh


def _calories_from_workout(w: Mapping[str, Any]) -> int | None:
    aeb = w.get("activeEnergyBurned")
    if isinstance(aeb, dict):
        kcal = _num(aeb.get("qty"))
        u = str(aeb.get("units") or "").lower()
        if kcal is not None:
            if "kj" in u:
                return int(round(kcal * KJ_TO_KCAL))
            return int(round(kcal))
    ae = w.get("activeEnergy")
    if isinstance(ae, dict):
        kcal = _num(ae.get("qty"))
        if kcal is not None:
            u = str(ae.get("units") or "").lower()
            if "kj" in u:
                return int(round(kcal * KJ_TO_KCAL))
            return int(round(kcal))
    te = w.get("totalEnergy")
    if isinstance(te, dict):
        k = _num(te.get("qty"))
        if k is not None:
            u = str(te.get("units") or "").lower()
            if "kj" in u:
                return int(round(k * KJ_TO_KCAL))
            if "kcal" in u or "cal" in u:
                return int(round(k))
    return None


def _avg_pace_sec_mi(duration_sec: float, miles: float | None) -> int | None:
    if miles is None or miles <= 1e-9:
        return None
    try:
        return int(round(duration_sec / miles))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _apple_source_id(workout: Mapping[str, Any], *, start_s: str, name_s: str) -> str:
    wid = workout.get("id")
    if isinstance(wid, str) and wid.strip():
        return f"apple_health:{wid.strip()}"
    if isinstance(wid, int | float):
        return f"apple_health:{int(wid)}"
    raw = f"{start_s}|{name_s}".encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()[:20]
    return f"apple_health:h:{h}"


def _normalize_workout_v2(w: Mapping[str, Any], user_id: str) -> dict[str, Any] | None:
    start = w.get("start")
    if not isinstance(start, str) or not start.strip():
        return None
    event_date = _parse_workout_timestamp(start)
    if event_date is None:
        return None

    name_raw = w.get("name")
    name_s = name_raw.strip() if isinstance(name_raw, str) else "Workout"
    activity_type = name_s[:200] if name_s else "Workout"

    dur_sec = _num(w.get("duration"))
    if dur_sec is None:
        end = w.get("end")
        if isinstance(end, str):
            dur_sec = _duration_sec_from_start_end(start, end)
    if dur_sec is None or dur_sec <= 0:
        return None
    duration_min = _duration_minutes_from_seconds(dur_sec)
    if duration_min is None:
        return None

    dist_mi = _quantity_to_miles(w.get("distance"))
    elev_ft = None
    eu = w.get("elevationUp")
    if isinstance(eu, dict):
        elev_ft = _quantity_to_feet(eu)
    elif isinstance(w.get("elevationGain"), dict):
        elev_ft = _quantity_to_feet(w.get("elevationGain"))

    avg_hr, max_hr = _heart_rate_block_avg_max(w)
    pace = _avg_pace_sec_mi(float(dur_sec), dist_mi)
    calories = _calories_from_workout(w)

    notes: str | None = name_s if name_s and name_s != "Workout" else None

    return {
        "user_id": user_id,
        "source": APPLE_HEALTH_CARDIO_SOURCE,
        "source_app": _source_app(w),
        "source_id": _apple_source_id(w, start_s=start.strip(), name_s=name_s),
        "event_date": event_date,
        "started_at": parse_iso_datetime_utc(start),
        "activity_type": activity_type,
        "duration_min": duration_min,
        "distance_miles": dist_mi,
        "elevation_ft": elev_ft,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_pace_sec_mi": pace,
        "calories": calories,
        "effort_zone": None,
        "session_rpe": None,
        "notes": notes,
    }


def _normalize_workout_v1(w: Mapping[str, Any], user_id: str) -> dict[str, Any] | None:
    start = w.get("start")
    if not isinstance(start, str) or not start.strip():
        return None
    event_date = _parse_workout_timestamp(start)
    if event_date is None:
        return None
    name_raw = w.get("name")
    name_s = name_raw.strip() if isinstance(name_raw, str) else "Workout"

    end = w.get("end")
    dur_sec = None
    if isinstance(end, str):
        dur_sec = _duration_sec_from_start_end(start, end)
    if dur_sec is None or dur_sec <= 0:
        return None
    duration_min = _duration_minutes_from_seconds(dur_sec)
    if duration_min is None:
        return None

    dist_mi = _quantity_to_miles(w.get("distance"))
    avg_hr = _optional_int_hr(_nested_qty(w.get("avgHeartRate")))
    max_hr = _optional_int_hr(_nested_qty(w.get("maxHeartRate")))
    pace = _avg_pace_sec_mi(float(dur_sec), dist_mi)
    calories = _calories_from_workout(w)

    return {
        "user_id": user_id,
        "source": APPLE_HEALTH_CARDIO_SOURCE,
        "source_app": _source_app(w),
        "source_id": _apple_source_id(w, start_s=start.strip(), name_s=name_s),
        "event_date": event_date,
        "started_at": parse_iso_datetime_utc(start),
        "activity_type": (name_s[:200] if name_s else "Workout"),
        "duration_min": duration_min,
        "distance_miles": dist_mi,
        "elevation_ft": None,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_pace_sec_mi": pace,
        "calories": calories,
        "effort_zone": None,
        "session_rpe": None,
        "notes": name_s if name_s != "Workout" else None,
    }


def _is_workout_v2_shape(w: Mapping[str, Any]) -> bool:
    """v2 includes a top-level ``duration`` in seconds; v1 relies on start/end only."""
    d = w.get("duration")
    return isinstance(d, (int, float)) and not isinstance(d, bool)


def normalize_hae_workouts(workouts: Any, user_id: str) -> list[dict[str, Any]]:
    """Map HAE ``data.workouts`` array to ``cardio_events`` row dicts."""
    if not isinstance(workouts, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in workouts:
        if not isinstance(item, dict):
            continue
        if _is_workout_v2_shape(item):
            row = _normalize_workout_v2(item, user_id)
        else:
            row = _normalize_workout_v1(item, user_id)
        if row is not None:
            rows.append(row)
    return rows


def extract_workouts_from_payload(body: Any) -> list[Any]:
    """Return the workouts array from a full HAE POST body, or []."""
    if isinstance(body, list):
        merged: list[Any] = []
        for part in body:
            merged.extend(extract_workouts_from_payload(part))
        return merged
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    if isinstance(data, dict):
        w = data.get("workouts")
        if isinstance(w, list):
            return w
    w2 = body.get("workouts")
    if isinstance(w2, list):
        return w2
    return []


def normalize_apple_health_cardio_from_payload(body: Any, user_id: str) -> list[dict[str, Any]]:
    """Convenience: extract workouts from HAE JSON and normalize."""
    return normalize_hae_workouts(extract_workouts_from_payload(body), user_id)

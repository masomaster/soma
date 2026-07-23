"""Parse activity files (FIT / TCX / GPX) into ``cardio_events`` rows with power.

Writes a **JSON raw envelope** (base64 payload) via :func:`pipeline.raw_storage.format_raw_object_key`
before normalizing — binary FIT is never stored as a bare ``.fit`` S3 key.

FIT parsing requires the optional ``fitdecode`` extra (``pip install -e '.[fit]'``).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.cardio_quality import FLAG_NO_POWER, assess_cardio_quality
from pipeline.power_math import (
    avg_and_max_watts,
    mean_maximal_power,
    normalized_power,
    work_kilojoules,
)
from pipeline.raw_storage import format_raw_object_key

logger = logging.getLogger(__name__)

SOURCE_WAHOO_FIT = "wahoo_fit"
SOURCE_STRAVA_EXPORT = "strava_export"
ALLOWED_SOURCES = frozenset({SOURCE_WAHOO_FIT, SOURCE_STRAVA_EXPORT})

METERS_PER_MILE = 1609.344
METERS_TO_FEET = 3.280839895
# FIT epoch: 1989-12-31 00:00:00 UTC
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

_SPORT_TO_ACTIVITY: dict[str, str] = {
    "cycling": "Ride",
    "cycling_indoor": "Ride",
    "cycling_outdoor": "Ride",
    "bike": "Ride",
    "biking": "Ride",
    "mountain_biking": "Ride",
    "gravel_cycling": "Ride",
    "running": "Run",
    "trail_running": "Run",
    "treadmill": "Run",
    "walking": "Walk",
    "hiking": "Hike",
    "swimming": "Swim",
    "lap_swimming": "Swim",
    "open_water_swimming": "Swim",
}


class FitDecodeUnavailableError(RuntimeError):
    """Raised when FIT bytes are supplied but ``fitdecode`` is not installed."""


def activity_source_id(
    source: str,
    *,
    started_at: datetime,
    activity_type: str,
    duration_sec: float,
) -> str:
    """Stable dedup key independent of filename (Strava renames exports)."""
    start = started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sport = re.sub(r"[^a-z0-9]+", "", activity_type.strip().lower()) or "workout"
    dur = int(round(duration_sec))
    digest = hashlib.sha256(f"{source}|{start}|{sport}|{dur}".encode()).hexdigest()[:16]
    return f"{source}:{digest}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def maybe_gunzip(data: bytes) -> bytes:
    """Return gunzipped bytes when payload looks like gzip; otherwise unchanged."""
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return gzip.decompress(data)
    return data


def raw_activity_envelope(
    *,
    filename: str,
    payload: bytes,
    source: str,
    content_sha256: str | None = None,
) -> dict[str, Any]:
    """JSON-serializable raw envelope for S3 (keeps ``.json`` key convention)."""
    return {
        "filename": filename,
        "source": source,
        "sha256": content_sha256 or sha256_hex(payload),
        "payload_base64": base64.b64encode(payload).decode("ascii"),
    }


def write_raw_envelope(
    *,
    user_id: str,
    source: str,
    filename: str,
    payload: bytes,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
) -> tuple[str, str]:
    """Persist raw envelope; return ``(s3_key, sha256)``."""
    digest = sha256_hex(payload)
    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    key = format_raw_object_key(user_id, source, at)
    body = json.dumps(
        raw_activity_envelope(
            filename=filename, payload=payload, source=source, content_sha256=digest
        ),
        separators=(",", ":"),
    ).encode("utf-8")
    raw_put(key, body)
    logger.info("Recorded raw activity envelope at key %s (sha256=%s)", key, digest[:12])
    return key, digest


def _sport_to_activity_type(raw: Any) -> str:
    if raw is None:
        return "Ride"
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # FIT sport enum: 2 = cycling commonly; keep numeric as Ride fallback.
        return "Ride" if int(raw) in {1, 2, 11} else "Workout"
    s = str(raw).strip().lower().replace(" ", "_")
    if not s:
        return "Ride"
    if s in _SPORT_TO_ACTIVITY:
        return _SPORT_TO_ACTIVITY[s]
    for key, label in _SPORT_TO_ACTIVITY.items():
        if key in s or s in key:
            return label
    return str(raw).strip().title() or "Workout"


def _fit_timestamp_to_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # FIT datetime is seconds since 1989-12-31; values below 0x10000000 are
        # "system" times — treat large values as FIT epoch offsets.
        n = int(value)
        if n >= 0x10000000:
            return _FIT_EPOCH + timedelta(seconds=n)
        return datetime.fromtimestamp(n, tz=timezone.utc)
    return None


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_fit_records(payload: bytes) -> dict[str, Any]:
    """Decode FIT bytes into session summary + 1 Hz-ish power list.

    Returns keys: ``started_at``, ``activity_type``, ``duration_sec``, ``distance_m``,
    ``elevation_m``, ``avg_hr``, ``max_hr``, ``watts``, ``device_watts``, ``name``.
    """
    try:
        import fitdecode
    except ImportError as exc:
        raise FitDecodeUnavailableError(
            "fitdecode is required to parse FIT files; install with: pip install -e '.[fit]'"
        ) from exc

    watts: list[float | None] = []
    hrs: list[int] = []
    distances: list[float] = []
    altitudes: list[float] = []
    timestamps: list[datetime] = []
    sport: Any = None
    session_name: str | None = None
    device_watts = True

    with fitdecode.FitReader(payload) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            name = frame.name
            if name == "sport":
                if frame.has_field("name"):
                    sport = frame.get_value("name")
                elif frame.has_field("sport"):
                    sport = frame.get_value("sport")
            elif name == "session":
                if sport is None and frame.has_field("sport"):
                    sport = frame.get_value("sport")
                if frame.has_field("sport_profile_name"):
                    session_name = str(frame.get_value("sport_profile_name") or "") or None
            elif name == "record":
                ts = None
                if frame.has_field("timestamp"):
                    ts = _fit_timestamp_to_dt(frame.get_value("timestamp"))
                if ts is not None:
                    timestamps.append(ts)
                if frame.has_field("power"):
                    raw_p = frame.get_value("power")
                    if raw_p is None:
                        watts.append(None)
                    else:
                        try:
                            watts.append(float(raw_p))
                        except (TypeError, ValueError):
                            watts.append(None)
                else:
                    # Keep timeline alignment when power field absent on this record.
                    if watts or timestamps:
                        watts.append(None)
                if frame.has_field("heart_rate"):
                    try:
                        hrs.append(int(frame.get_value("heart_rate")))
                    except (TypeError, ValueError):
                        pass
                if frame.has_field("distance"):
                    try:
                        distances.append(float(frame.get_value("distance")))
                    except (TypeError, ValueError):
                        pass
                if frame.has_field("altitude"):
                    try:
                        altitudes.append(float(frame.get_value("altitude")))
                    except (TypeError, ValueError):
                        pass
                elif frame.has_field("enhanced_altitude"):
                    try:
                        altitudes.append(float(frame.get_value("enhanced_altitude")))
                    except (TypeError, ValueError):
                        pass

    started_at = timestamps[0] if timestamps else None
    ended_at = timestamps[-1] if timestamps else None
    duration_sec = 0.0
    if started_at and ended_at:
        duration_sec = max(0.0, (ended_at - started_at).total_seconds())
    # Resample sparse records onto ~1s grid when timestamps exist.
    power_series = _align_power_series(timestamps, watts)

    distance_m = distances[-1] if distances else None
    elev_m = None
    if len(altitudes) >= 2:
        gain = 0.0
        for a, b in zip(altitudes, altitudes[1:], strict=False):
            if b > a:
                gain += b - a
        elev_m = gain
    avg_hr = int(round(sum(hrs) / len(hrs))) if hrs else None
    max_hr = max(hrs) if hrs else None

    return {
        "started_at": started_at,
        "activity_type": _sport_to_activity_type(sport),
        "duration_sec": duration_sec or float(len(power_series)),
        "distance_m": distance_m,
        "elevation_m": elev_m,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "watts": power_series,
        "device_watts": device_watts,
        "name": session_name,
    }


def _align_power_series(
    timestamps: Sequence[datetime], watts: Sequence[float | None]
) -> list[float | None]:
    """Build an approximately 1 Hz power series from record timestamps."""
    if not timestamps:
        return list(watts)
    # Pair each timestamp with its power (watts list may be shorter).
    pairs: list[tuple[datetime, float | None]] = []
    for i, ts in enumerate(timestamps):
        w = watts[i] if i < len(watts) else None
        pairs.append((ts, w))
    if len(pairs) == 1:
        return [pairs[0][1]]
    start = pairs[0][0]
    end = pairs[-1][0]
    total = int(max(0, round((end - start).total_seconds()))) + 1
    series: list[float | None] = [None] * total
    for ts, w in pairs:
        idx = int(round((ts - start).total_seconds()))
        if 0 <= idx < total:
            series[idx] = w
    # Forward-fill small gaps (≤2s) so MMP windows are contiguous.
    last: float | None = None
    gap = 0
    for i, w in enumerate(series):
        if w is not None:
            last = w
            gap = 0
        elif last is not None and gap < 2:
            series[i] = last
            gap += 1
        else:
            gap += 1
            if gap > 2:
                last = None
    return series


def _local(tag: str) -> str:
    """Strip XML namespace for comparisons."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_tcx(payload: bytes) -> dict[str, Any]:
    """Extract session + power from a TCX document."""
    root = ET.fromstring(payload)
    watts: list[float | None] = []
    hrs: list[int] = []
    distances: list[float] = []
    altitudes: list[float] = []
    timestamps: list[datetime] = []
    activity_type = "Ride"
    name: str | None = None

    for elem in root.iter():
        ln = _local(elem.tag)
        if ln == "Activity" and elem.get("Sport"):
            activity_type = _sport_to_activity_type(elem.get("Sport"))
        elif ln == "Name" and name is None and elem.text:
            name = elem.text.strip() or None
        elif ln == "Trackpoint":
            ts = None
            dist = None
            alt = None
            hr = None
            power = None
            for child in elem:
                cl = _local(child.tag)
                if cl == "Time":
                    ts = _parse_iso_dt(child.text)
                elif cl == "DistanceMeters" and child.text:
                    try:
                        dist = float(child.text)
                    except ValueError:
                        pass
                elif cl == "AltitudeMeters" and child.text:
                    try:
                        alt = float(child.text)
                    except ValueError:
                        pass
                elif cl == "HeartRateBpm":
                    for v in child:
                        if _local(v.tag) == "Value" and v.text:
                            try:
                                hr = int(float(v.text))
                            except ValueError:
                                pass
                elif cl in {"Cadence", "Extensions"}:
                    # Power often lives under Extensions / TPX / Watts
                    for sub in child.iter():
                        if _local(sub.tag).lower() in {"watts", "power"} and sub.text:
                            try:
                                power = float(sub.text)
                            except ValueError:
                                pass
            if ts is not None:
                timestamps.append(ts)
                watts.append(power)
                if dist is not None:
                    distances.append(dist)
                if alt is not None:
                    altitudes.append(alt)
                if hr is not None:
                    hrs.append(hr)

    started_at = timestamps[0] if timestamps else None
    ended_at = timestamps[-1] if timestamps else None
    duration_sec = (
        max(0.0, (ended_at - started_at).total_seconds()) if started_at and ended_at else 0.0
    )
    elev_m = None
    if len(altitudes) >= 2:
        elev_m = sum(max(0.0, b - a) for a, b in zip(altitudes, altitudes[1:], strict=False))
    return {
        "started_at": started_at,
        "activity_type": activity_type,
        "duration_sec": duration_sec or float(len(watts)),
        "distance_m": distances[-1] if distances else None,
        "elevation_m": elev_m,
        "avg_hr": int(round(sum(hrs) / len(hrs))) if hrs else None,
        "max_hr": max(hrs) if hrs else None,
        "watts": _align_power_series(timestamps, watts) if timestamps else watts,
        "device_watts": True,
        "name": name,
    }


def parse_gpx(payload: bytes) -> dict[str, Any]:
    """Extract session + power from GPX (Garmin TrackPointExtension watts when present)."""
    root = ET.fromstring(payload)
    watts: list[float | None] = []
    hrs: list[int] = []
    altitudes: list[float] = []
    timestamps: list[datetime] = []
    name: str | None = None
    activity_type = "Ride"

    for elem in root.iter():
        ln = _local(elem.tag)
        if ln == "name" and name is None and elem.text:
            name = elem.text.strip() or None
        elif ln == "type" and elem.text:
            activity_type = _sport_to_activity_type(elem.text)
        elif ln == "trkpt":
            ts = None
            elev = None
            hr = None
            power = None
            for child in elem:
                cl = _local(child.tag)
                if cl == "time":
                    ts = _parse_iso_dt(child.text)
                elif cl == "ele" and child.text:
                    try:
                        elev = float(child.text)
                    except ValueError:
                        pass
                elif cl == "extensions":
                    for sub in child.iter():
                        sl = _local(sub.tag).lower()
                        if sl in {"hr", "heartrate"} and sub.text:
                            try:
                                hr = int(float(sub.text))
                            except ValueError:
                                pass
                        if sl in {"power", "watts"} and sub.text:
                            try:
                                power = float(sub.text)
                            except ValueError:
                                pass
            if ts is not None:
                timestamps.append(ts)
                watts.append(power)
                if elev is not None:
                    altitudes.append(elev)
                if hr is not None:
                    hrs.append(hr)

    started_at = timestamps[0] if timestamps else None
    ended_at = timestamps[-1] if timestamps else None
    duration_sec = (
        max(0.0, (ended_at - started_at).total_seconds()) if started_at and ended_at else 0.0
    )
    # Haversine omitted — distance left None unless we add it later; elevation gain OK.
    elev_m = None
    if len(altitudes) >= 2:
        elev_m = sum(max(0.0, b - a) for a, b in zip(altitudes, altitudes[1:], strict=False))
    return {
        "started_at": started_at,
        "activity_type": activity_type,
        "duration_sec": duration_sec or float(len(watts)),
        "distance_m": None,
        "elevation_m": elev_m,
        "avg_hr": int(round(sum(hrs) / len(hrs))) if hrs else None,
        "max_hr": max(hrs) if hrs else None,
        "watts": _align_power_series(timestamps, watts) if timestamps else watts,
        "device_watts": any(w is not None for w in watts),
        "name": name,
    }


def parse_activity_bytes(payload: bytes, *, filename: str = "") -> dict[str, Any]:
    """Dispatch by filename suffix / content sniffing after optional gunzip."""
    data = maybe_gunzip(payload)
    lower = filename.lower()
    if lower.endswith(".fit") or lower.endswith(".fit.gz") or (
        len(data) >= 12 and data[8:12] == b".FIT"
    ):
        return parse_fit_records(data)
    if lower.endswith(".tcx") or lower.endswith(".tcx.gz") or b"<TrainingCenterDatabase" in data[:500]:
        return parse_tcx(data)
    if lower.endswith(".gpx") or lower.endswith(".gpx.gz") or b"<gpx" in data[:500].lower():
        return parse_gpx(data)
    # Default: try FIT magic, then TCX, then GPX.
    if len(data) >= 12 and data[8:12] == b".FIT":
        return parse_fit_records(data)
    if b"<TrainingCenterDatabase" in data[:2000]:
        return parse_tcx(data)
    if b"<gpx" in data[:2000].lower():
        return parse_gpx(data)
    raise ValueError(f"Unrecognized activity file format for {filename!r}")


def session_to_cardio_row(
    session: Mapping[str, Any],
    *,
    user_id: str,
    source: str,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """Map a parsed session dict to a ``cardio_events`` row."""
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {sorted(ALLOWED_SOURCES)}")
    started_at = session.get("started_at")
    if not isinstance(started_at, datetime):
        return None
    duration_sec = float(session.get("duration_sec") or 0.0)
    if duration_sec <= 0:
        return None
    activity_type = str(session.get("activity_type") or "Ride")
    watts = session.get("watts")
    if not isinstance(watts, list):
        watts = []
    avg_w, max_w = avg_and_max_watts(watts)
    has_power = any(w is not None for w in watts)
    mmp = mean_maximal_power(watts) if has_power else {}
    np_val = normalized_power(watts) if has_power else None
    work = work_kilojoules(watts) if has_power else None
    dist_m = session.get("distance_m")
    dist_mi = round(float(dist_m) / METERS_PER_MILE, 4) if dist_m is not None else None
    elev_m = session.get("elevation_m")
    elev_ft = round(float(elev_m) * METERS_TO_FEET, 2) if elev_m is not None else None
    event_date: date = started_at.astimezone(timezone.utc).date()
    note = notes
    if note is None and isinstance(session.get("name"), str) and session["name"].strip():
        note = session["name"].strip()

    row: dict[str, Any] = {
        "user_id": user_id,
        "source": source,
        "source_app": None,
        "source_id": activity_source_id(
            source,
            started_at=started_at,
            activity_type=activity_type,
            duration_sec=duration_sec,
        ),
        "event_date": event_date,
        "started_at": started_at,
        "activity_type": activity_type,
        "duration_min": round(duration_sec / 60.0, 4),
        "distance_miles": dist_mi,
        "elevation_ft": elev_ft,
        "avg_hr": session.get("avg_hr"),
        "max_hr": session.get("max_hr"),
        "avg_pace_sec_mi": (
            int(round(duration_sec / dist_mi)) if dist_mi and dist_mi > 1e-9 else None
        ),
        "calories": None,
        "effort_zone": None,
        "session_rpe": None,
        "notes": note,
        "avg_watts": avg_w,
        "max_watts": max_w,
        "normalized_power": np_val,
        "work_kj": work,
        "device_watts": bool(session.get("device_watts")) if has_power else None,
        "power_mmp_json": mmp or None,
    }
    flags = assess_cardio_quality(row)
    if not has_power:
        flags.append(FLAG_NO_POWER)
    row["quality_flags"] = flags or None
    return row


def fetch_and_normalize(
    user_id: str,
    *,
    source: str,
    filename: str,
    payload: bytes,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    notes: str | None = None,
) -> list[dict[str, Any]]:
    """Write raw envelope, parse activity bytes, return zero or one cardio row.

    Docstring required for adapter ``fetch_and_normalize`` entry points.
    """
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {sorted(ALLOWED_SOURCES)}")
    data = maybe_gunzip(payload)
    write_raw_envelope(
        user_id=user_id,
        source=source,
        filename=filename,
        payload=data,
        raw_put=raw_put,
        utc_now=utc_now,
    )
    session = parse_activity_bytes(data, filename=filename)
    row = session_to_cardio_row(session, user_id=user_id, source=source, notes=notes)
    return [row] if row is not None else []


def discover_activity_files(root: Path) -> list[Path]:
    """Find activity files under ``root`` (Strava ``activities/`` or Dropbox folder)."""
    root = root.expanduser().resolve()
    if not root.exists():
        return []
    candidates = [
        root / "activities",
        root,
    ]
    found: list[Path] = []
    seen: set[Path] = set()
    patterns = ("*.fit", "*.fit.gz", "*.FIT", "*.tcx", "*.tcx.gz", "*.gpx", "*.gpx.gz")
    for base in candidates:
        if not base.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(base.glob(pattern)):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    found.append(path)
            # One level of nesting (some exports nest by month).
            for path in sorted(base.glob(f"*/{pattern}")):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    found.append(path)
    return found


def load_strava_activity_titles(export_root: Path) -> dict[str, str]:
    """Map ``activities.csv`` filename stem → activity name when the CSV exists."""
    csv_path = export_root / "activities.csv"
    if not csv_path.is_file():
        # Sometimes CSV sits next to activities/
        alt = export_root.parent / "activities.csv"
        csv_path = alt if alt.is_file() else csv_path
    if not csv_path.is_file():
        return {}
    import csv

    titles: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Common columns: Filename, Name / Activity Name
            filename = (row.get("Filename") or row.get("filename") or "").strip()
            name = (row.get("Name") or row.get("Activity Name") or row.get("name") or "").strip()
            if not filename or not name:
                continue
            stem = Path(filename).name
            titles[stem] = name
            titles[Path(stem).stem] = name
    return titles

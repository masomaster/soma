"""Strava API v3: fetch athlete activities, write raw JSON, normalize to ``cardio_events`` rows.

HTTP calls use only ``STRAVA_API_BASE`` (see :func:`_validate_strava_base_url`) to avoid SSRF.
Tests monkeypatch ``urllib.request.urlopen`` or inject ``fetch_all_pages`` into
:func:`fetch_and_normalize`.

On HTTP / transport failures, :func:`fetch_strava_activities_page` raises
:class:`StravaRequestError` with ``status_code`` and ``retry_suggested`` so callers
can backoff. Error JSON bodies from Strava are not logged in full (only length)
to reduce accidental PII leakage.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pipeline.cardio_quality import assess_cardio_quality
from pipeline.raw_storage import format_raw_object_key
from pipeline.timeparse import parse_iso_datetime_utc

logger = logging.getLogger(__name__)


class StravaRequestError(Exception):
    """Strava API HTTP failure or transport error (DNS, TLS, timeout, etc.)."""

    __slots__ = ("retry_suggested", "status_code")

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_suggested: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_suggested = retry_suggested


_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})

STRAVA_API_BASE = "https://www.strava.com/api/v3"
CARDIO_SOURCE = "strava"
METERS_PER_MILE = 1609.344
METERS_TO_FEET = 3.280839895
KJ_TO_KCAL = 0.239006
# Cap pagination to avoid unbounded HTTP + memory if ``per_page`` is large.
MAX_STRAVA_ACTIVITY_PAGES = 500
# Strava allows up to 200 per page; default lower for gentler rate limits.
DEFAULT_PER_PAGE = 50


def _normalize_api_base(url: str) -> str:
    return url.rstrip("/")


def _validate_strava_base_url(base_url: str) -> str:
    """Allow only the official Strava API origin (mitigate SSRF via ``base_url``)."""
    normalized = _normalize_api_base(base_url)
    allowed = _normalize_api_base(STRAVA_API_BASE)
    if normalized != allowed:
        raise ValueError(
            f"Strava base_url must be {STRAVA_API_BASE!r} after trimming trailing slashes; "
            f"got {base_url!r}"
        )
    return normalized


def strava_source_id(activity_id: int) -> str:
    """Dedup key: one row per Strava activity (summary list endpoint)."""
    return f"strava:{activity_id}"


def _parse_event_date(obj: Mapping[str, Any]) -> date | None:
    """Prefer ``start_date_local`` (athlete-local wall time) for calendar ``event_date``."""
    for key in ("start_date_local", "start_date"):
        raw = obj.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        s = raw.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            continue
    return None


def _parse_start_dt(obj: Mapping[str, Any]) -> datetime | None:
    """Timezone-aware workout start (prefer UTC ``start_date``)."""
    for key in ("start_date", "start_date_local"):
        dt = parse_iso_datetime_utc(obj.get(key))
        if dt is not None:
            return dt
    return None


def _num_minutes_from_moving_time(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    try:
        sec = float(raw)
    except (TypeError, ValueError):
        return None
    if sec < 0:
        return None
    return round(sec / 60.0, 4)


def _distance_miles(raw_meters: Any) -> float | None:
    if raw_meters is None:
        return None
    if isinstance(raw_meters, bool):
        return None
    try:
        m = float(raw_meters)
    except (TypeError, ValueError):
        return None
    if m < 0:
        return None
    return round(m / METERS_PER_MILE, 4)


def _elevation_ft(raw_meters: Any) -> float | None:
    if raw_meters is None:
        return None
    if isinstance(raw_meters, bool):
        return None
    try:
        m = float(raw_meters)
    except (TypeError, ValueError):
        return None
    if m < 0:
        return None
    return round(m * METERS_TO_FEET, 2)


def _optional_int_hr(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _optional_calories(obj: Mapping[str, Any]) -> int | None:
    cal = obj.get("calories")
    if cal is not None and not isinstance(cal, bool):
        try:
            return int(round(float(cal)))
        except (TypeError, ValueError):
            pass
    kj = obj.get("kilojoules")
    if kj is not None and not isinstance(kj, bool):
        try:
            return int(round(float(kj) * KJ_TO_KCAL))
        except (TypeError, ValueError):
            pass
    return None


def _avg_pace_sec_mi(duration_sec: float, miles: float | None) -> int | None:
    if miles is None or miles <= 1e-9:
        return None
    try:
        return int(round(duration_sec / miles))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def normalize_strava_activities(activities: Any, user_id: str) -> list[dict[str, Any]]:
    """Map a Strava ``GET /athlete/activities`` JSON array to ``cardio_events`` row dicts."""
    if not isinstance(activities, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in activities:
        if not isinstance(item, dict):
            continue
        row = _normalize_strava_activity(item, user_id)
        if row is not None:
            row["quality_flags"] = assess_cardio_quality(row) or None
            rows.append(row)
    return rows


def _normalize_strava_activity(obj: Mapping[str, Any], user_id: str) -> dict[str, Any] | None:
    aid = obj.get("id")
    if isinstance(aid, bool) or not isinstance(aid, int | float):
        return None
    activity_id = int(aid)
    event_date = _parse_event_date(obj)
    if event_date is None:
        return None
    act_type = obj.get("type")
    if not isinstance(act_type, str) or not act_type.strip():
        return None
    moving = obj.get("moving_time")
    duration_min = _num_minutes_from_moving_time(moving)
    if duration_min is None:
        return None
    try:
        moving_sec = float(moving)
    except (TypeError, ValueError):
        return None

    dist_mi = _distance_miles(obj.get("distance"))
    elev_ft = _elevation_ft(obj.get("total_elevation_gain"))
    avg_hr = _optional_int_hr(obj.get("average_heartrate"))
    max_hr = _optional_int_hr(obj.get("max_heartrate"))
    pace = _avg_pace_sec_mi(moving_sec, dist_mi)
    calories = _optional_calories(obj)
    name = obj.get("name")
    notes: str | None
    if isinstance(name, str) and name.strip():
        notes = name.strip()
    else:
        notes = None

    return {
        "user_id": user_id,
        "source": CARDIO_SOURCE,
        "source_app": None,
        "source_id": strava_source_id(activity_id),
        "event_date": event_date,
        "started_at": _parse_start_dt(obj),
        "activity_type": act_type.strip(),
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


def fetch_and_normalize(
    user_id: str,
    *,
    fetch_all_pages: Callable[[], list[list[Any]]],
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
) -> list[dict[str, Any]]:
    """Fetch activity pages, persist each page as raw JSON, return merged normalized rows.

    ``fetch_all_pages`` returns a list of pages, each page a JSON array (Strava's shape).
    Tests inject a lambda that returns fixture lists.
    """
    pages = fetch_all_pages()
    merged: list[dict[str, Any]] = []
    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    for i, page in enumerate(pages):
        key = format_raw_object_key(user_id, CARDIO_SOURCE, at + timedelta(microseconds=i))
        body = json.dumps(page, separators=(",", ":"), default=str).encode("utf-8")
        raw_put(key, body)
        logger.info("Recorded raw Strava activities page at key %s", key)
        merged.extend(normalize_strava_activities(page, user_id))
    return merged


def _decode_strava_activities_json(raw: bytes) -> list[Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.error("Strava response was not valid UTF-8 (%s bytes)", len(raw))
        raise ValueError("Strava response was not valid UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error(
            "Strava response was not valid JSON (len=%s, line=%s, col=%s)",
            len(text),
            exc.lineno,
            exc.colno,
        )
        raise ValueError("Strava response was not valid JSON") from exc
    if isinstance(data, dict):
        # Typical OAuth / validation error payload — not a list of activities.
        logger.error(
            "Strava activities response was a JSON object (%s bytes), not an array",
            len(text),
        )
        raise ValueError("Strava activities response must be a JSON array")
    if not isinstance(data, list):
        raise ValueError("Strava activities response must be a JSON array")
    return data


def fetch_strava_activities_page(
    access_token: str,
    *,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    before: int | None = None,
    after: int | None = None,
    base_url: str = STRAVA_API_BASE,
) -> list[Any]:
    """Perform ``GET {base_url}/athlete/activities`` with Bearer token.

    Raises:
        ValueError: Invalid ``base_url`` or response is not UTF-8 / JSON array.
        StravaRequestError: HTTP error or transport failure (see ``status_code``,
            ``retry_suggested``).
    """
    base = _validate_strava_base_url(base_url)
    if page < 1:
        raise ValueError("page must be >= 1")
    if per_page < 1 or per_page > 200:
        raise ValueError("per_page must be between 1 and 200 (Strava API limit)")
    q = f"{base}/athlete/activities?page={page}&per_page={per_page}"
    if before is not None:
        q += f"&before={int(before)}"
    if after is not None:
        q += f"&after={int(after)}"
    req = urllib.request.Request(
        q,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        code = exc.code
        logger.error("Strava HTTP error %s: %s", code, exc.reason)
        retry = code in _RETRYABLE_HTTP
        raise StravaRequestError(
            f"Strava API returned HTTP {code} {exc.reason!r}",
            status_code=code,
            retry_suggested=retry,
        ) from exc
    except urllib.error.URLError as exc:
        logger.error("Strava request failed: %s", exc.reason)
        raise StravaRequestError(
            f"Strava request failed: {exc.reason!r}",
            status_code=None,
            retry_suggested=True,
        ) from exc
    return _decode_strava_activities_json(raw)


def build_fetch_all_activity_pages(
    access_token: str,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    before: int | None = None,
    after: int | None = None,
    max_pages: int = MAX_STRAVA_ACTIVITY_PAGES,
) -> Callable[[], list[list[Any]]]:
    """Return a callable that walks ``page`` until a short page or empty response."""

    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    def _fetch_all() -> list[list[Any]]:
        pages: list[list[Any]] = []
        for p in range(1, max_pages + 1):
            batch = fetch_strava_activities_page(
                access_token,
                page=p,
                per_page=per_page,
                before=before,
                after=after,
            )
            pages.append(batch)
            if len(batch) < per_page:
                break
        else:
            raise RuntimeError(
                "Strava activity pagination did not complete within "
                f"max_pages={max_pages} (last page had per_page={per_page} activities)"
            )
        return pages

    return _fetch_all


def fetch_and_normalize_from_api(
    user_id: str,
    access_token: str,
    *,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    per_page: int = DEFAULT_PER_PAGE,
    before: int | None = None,
    after: int | None = None,
    max_pages: int = MAX_STRAVA_ACTIVITY_PAGES,
) -> list[dict[str, Any]]:
    """Convenience: paginate Strava activities API, raw-write each page, normalize."""
    return fetch_and_normalize(
        user_id,
        fetch_all_pages=build_fetch_all_activity_pages(
            access_token,
            per_page=per_page,
            before=before,
            after=after,
            max_pages=max_pages,
        ),
        raw_put=raw_put,
        utc_now=utc_now,
    )

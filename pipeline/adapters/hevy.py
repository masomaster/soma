"""Hevy Pro API: fetch workouts, write raw JSON, normalize to ``strength_events`` rows.

HTTP calls use only ``HEVY_API_BASE`` (see :func:`_validate_hevy_base_url`) to avoid SSRF.
Contract tests or local mirrors cannot use a custom ``base_url``; monkeypatch
``urllib.request.urlopen`` or :func:`fetch_hevy_workouts_page` instead.

On HTTP / transport failures, :func:`fetch_hevy_workouts_page` raises
:class:`HevyRequestError` with ``status_code`` and ``retry_suggested`` so Lambdas
can backoff without parsing ``urllib`` internals. Response bodies are not logged
on JSON parse errors (only length and parse position) to reduce accidental PII
or token leakage in logs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from pipeline.raw_storage import format_raw_object_key

logger = logging.getLogger(__name__)


class HevyRequestError(Exception):
    """Hevy API HTTP failure or transport error (DNS, TLS, timeout, etc.)."""

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

HEVY_API_BASE = "https://api.hevyapp.com/v1"
STRENGTH_SOURCE = "hevy"
KG_TO_LBS = 2.2046226218
# Cap pagination to avoid unbounded HTTP + memory if API misreports page_count.
MAX_HEVY_WORKOUT_PAGES = 500


def _normalize_api_base(url: str) -> str:
    return url.rstrip("/")


def _validate_hevy_base_url(base_url: str) -> str:
    """Allow only the official Hevy API origin (mitigate SSRF via ``base_url``)."""
    normalized = _normalize_api_base(base_url)
    allowed = _normalize_api_base(HEVY_API_BASE)
    if normalized != allowed:
        raise ValueError(
            f"Hevy base_url must be {HEVY_API_BASE!r} after trimming trailing slashes; "
            f"got {base_url!r}"
        )
    return normalized


def _decode_hevy_json_body(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.error("Hevy response was not valid UTF-8 (%s bytes)", len(raw))
        raise ValueError("Hevy response was not valid UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error(
            "Hevy response was not valid JSON (len=%s, line=%s, col=%s)",
            len(text),
            exc.lineno,
            exc.colno,
        )
        raise ValueError("Hevy response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Hevy workouts response must be a JSON object")
    return data


def _coerce_page_count(payload: Mapping[str, Any], *, page: int) -> int:
    raw = payload.get("page_count", 1)
    if isinstance(raw, bool):
        raise ValueError(f"Hevy page_count must be numeric, not bool (page {page})")
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Hevy page_count not coercible to int (page {page}): {raw!r}"
        ) from exc
    if n < 1:
        logger.warning("Hevy page_count=%s invalid (<1) on page %s; using 1", n, page)
        return 1
    return n


def _hevy_set_type_to_db(set_type_raw: str) -> str:
    raw = (set_type_raw or "").strip().lower()
    if raw == "normal":
        return "working"
    if raw in {"warmup", "dropset", "working"}:
        return raw
    return raw or "working"


def _weight_kg_to_lbs(weight_kg: float | None) -> float | None:
    if weight_kg is None:
        return None
    return round(float(weight_kg) * KG_TO_LBS, 4)


def hevy_source_id(workout_id: str, exercise_index: int, set_index: int) -> str:
    """Dedup key aligned with ``integrations-checklist.md``."""
    return f"hevy:{workout_id}:{exercise_index}:{set_index}"


def normalize_hevy_list_workouts(payload: Mapping[str, Any], user_id: str) -> list[dict[str, Any]]:
    """Map a single ``GET /v1/workouts`` JSON object to ``strength_events`` row dicts."""
    workouts = payload.get("workouts")
    if not isinstance(workouts, list):
        return []
    rows: list[dict[str, Any]] = []
    for workout in workouts:
        if not isinstance(workout, dict):
            continue
        workout_id = workout.get("id")
        start_time = workout.get("start_time")
        if not isinstance(workout_id, str) or not isinstance(start_time, str):
            continue
        event_date = datetime.fromisoformat(start_time.replace("Z", "+00:00")).date()
        exercises = workout.get("exercises")
        if not isinstance(exercises, list):
            continue
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            title = exercise.get("title")
            ex_index = exercise.get("index")
            if not isinstance(title, str) or not isinstance(ex_index, int):
                continue
            superset_id = exercise.get("superset_id")
            if superset_id is not None and not isinstance(superset_id, int):
                superset_id = None
            sets = exercise.get("sets")
            if not isinstance(sets, list):
                continue
            for s in sets:
                if not isinstance(s, dict):
                    continue
                si = s.get("index")
                if not isinstance(si, int):
                    continue
                reps = s.get("reps")
                reps_i = int(reps) if isinstance(reps, int | float) else None
                rpe = s.get("rpe")
                rpe_f = float(rpe) if isinstance(rpe, int | float) else None
                st_raw = s.get("type")
                st = _hevy_set_type_to_db(st_raw) if isinstance(st_raw, str) else "working"
                wkg = s.get("weight_kg")
                wkg_f = float(wkg) if isinstance(wkg, int | float) else None
                rows.append(
                    {
                        "user_id": user_id,
                        "source": STRENGTH_SOURCE,
                        "source_id": hevy_source_id(workout_id, ex_index, si),
                        "event_date": event_date,
                        "exercise_name": title,
                        "muscle_group": None,
                        "movement_type": None,
                        "superset_id": superset_id,
                        "set_number": si + 1,
                        "reps": reps_i,
                        "weight_lbs": _weight_kg_to_lbs(wkg_f),
                        "rpe": rpe_f,
                        "set_type": st,
                        "notes": None,
                    }
                )
    return rows


def fetch_and_normalize(
    user_id: str,
    *,
    fetch_all_pages: Callable[[], list[dict[str, Any]]],
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
) -> list[dict[str, Any]]:
    """Fetch workout pages, persist each page as raw JSON, return merged normalized rows.

    ``fetch_all_pages`` typically calls :func:`fetch_hevy_workouts_page` in a loop
    until ``page >= page_count``. Tests inject a lambda that returns fixture dicts.

    ``raw_put`` receives ``(s3_key, utf-8 json bytes)`` — wire it to ``boto3``
    ``put_object`` in Lambda or to a no-op / local sink in tests.
    """
    pages = fetch_all_pages()
    merged: list[dict[str, Any]] = []
    at = utc_now if utc_now.tzinfo else utc_now.replace(tzinfo=timezone.utc)
    for i, page in enumerate(pages):
        key = format_raw_object_key(user_id, STRENGTH_SOURCE, at + timedelta(microseconds=i))
        body = json.dumps(page, separators=(",", ":"), default=str).encode("utf-8")
        raw_put(key, body)
        logger.info("Recorded raw Hevy page at key %s", key)
        merged.extend(normalize_hevy_list_workouts(page, user_id))
    return merged


def fetch_hevy_workouts_page(
    api_key: str,
    *,
    page: int = 1,
    page_size: int = 10,
    base_url: str = HEVY_API_BASE,
) -> dict[str, Any]:
    """Perform ``GET {base_url}/workouts`` with Hevy ``api-key`` header.

    Raises:
        ValueError: Invalid ``base_url`` or response is not UTF-8 / JSON object.
        HevyRequestError: HTTP error or transport failure (see ``status_code``,
            ``retry_suggested``).
    """
    base = _validate_hevy_base_url(base_url)
    url = f"{base}/workouts?page={page}&pageSize={page_size}"
    req = urllib.request.Request(
        url,
        headers={
            "api-key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        code = exc.code
        logger.error("Hevy HTTP error %s: %s", code, exc.reason)
        retry = code in _RETRYABLE_HTTP
        raise HevyRequestError(
            f"Hevy API returned HTTP {code} {exc.reason!r}",
            status_code=code,
            retry_suggested=retry,
        ) from exc
    except urllib.error.URLError as exc:
        logger.error("Hevy request failed: %s", exc.reason)
        raise HevyRequestError(
            f"Hevy request failed: {exc.reason!r}",
            status_code=None,
            retry_suggested=True,
        ) from exc
    return _decode_hevy_json_body(raw)


def build_fetch_all_workout_pages(
    api_key: str,
    *,
    max_pages: int = MAX_HEVY_WORKOUT_PAGES,
) -> Callable[[], list[dict[str, Any]]]:
    """Return a callable that walks ``page`` … ``page_count`` and collects each page."""

    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    def _fetch_all() -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        for p in range(1, max_pages + 1):
            payload = fetch_hevy_workouts_page(api_key, page=p)
            pages.append(payload)
            page_count = _coerce_page_count(payload, page=p)
            if p >= page_count:
                break
        else:
            last_pc = _coerce_page_count(pages[-1], page=max_pages) if pages else None
            raise RuntimeError(
                "Hevy workout pagination did not complete within "
                f"max_pages={max_pages} (last reported page_count={last_pc})"
            )
        return pages

    return _fetch_all


def fetch_and_normalize_from_api(
    user_id: str,
    api_key: str,
    *,
    raw_put: Callable[[str, bytes], None],
    utc_now: datetime,
    max_pages: int = MAX_HEVY_WORKOUT_PAGES,
) -> list[dict[str, Any]]:
    """Convenience: paginate Hevy API, raw-write each page, normalize."""
    return fetch_and_normalize(
        user_id,
        fetch_all_pages=build_fetch_all_workout_pages(api_key, max_pages=max_pages),
        raw_put=raw_put,
        utc_now=utc_now,
    )

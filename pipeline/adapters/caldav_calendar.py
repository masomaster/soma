"""iCloud CalDAV busy blocks → ``interventions`` rows (Phase 7).

Parses simplified VEVENT dicts (from CalDAV client or fixtures) into coaching
context rows. Use ``CALDAV_CALENDAR_NAME`` to poll only the user's calendar (e.g.
``Mason``) — shared/partner calendars (e.g. ``Caroline``) are intentionally excluded
because those events do not necessarily block the user's training window.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

CALDAV_SOURCE = "caldav_icloud"
DEFAULT_CATEGORY = "calendar_busy"


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    return None


def normalize_caldav_events(events: Sequence[Mapping[str, Any]], user_id: str) -> list[dict[str, Any]]:
    """Map CalDAV/VEVENT summaries to ``interventions`` insert dicts."""
    rows: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start = _parse_date(ev.get("start") or ev.get("dtstart"))
        if start is None:
            continue
        title = ev.get("summary") or ev.get("title")
        if not isinstance(title, str) or not title.strip():
            title = "Busy"
        end = _parse_date(ev.get("end") or ev.get("dtend"))
        desc = title.strip()
        if end is not None and end != start:
            desc = f"{desc} ({start.isoformat()} – {end.isoformat()})"
        rows.append(
            {
                "user_id": user_id,
                "event_date": start,
                "category": DEFAULT_CATEGORY,
                "description": desc[:500],
                "is_ongoing": False,
                "end_date": end,
                "notes": CALDAV_SOURCE,
            }
        )
    return rows


def _import_caldav():
    try:
        import caldav
    except ImportError as exc:
        raise OSError("caldav package not available") from exc
    return caldav


def _calendar_display_name(cal: Any) -> str:
    get_display = getattr(cal, "get_display_name", None)
    if callable(get_display):
        try:
            val = get_display()
            if val is not None:
                text = str(val).strip()
                if text:
                    return text
        except Exception:
            logger.debug("CalDAV: get_display_name failed for %r", cal, exc_info=True)
    name = getattr(cal, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return str(cal)


def _calendar_matches(cal: Any, calendar_name: str | None) -> bool:
    if not calendar_name:
        return True
    display = _calendar_display_name(cal).casefold()
    want = calendar_name.strip().casefold()
    return display == want or want in display


def _vevent_to_dict(ev: Any) -> dict[str, Any] | None:
    """Parse a caldav 3.x ``Event`` (icalendar). Returns None when DTSTART is missing."""
    get_ical = getattr(ev, "get_icalendar_component", None)
    comp = get_ical() if callable(get_ical) else getattr(ev, "icalendar_component", None)
    if comp is None:
        return None

    dtstart = comp.get("DTSTART")
    if dtstart is None:
        return None
    start = dtstart.dt if hasattr(dtstart, "dt") else dtstart
    dtend = comp.get("DTEND")
    end = dtend.dt if dtend is not None and hasattr(dtend, "dt") else dtend
    return {
        "summary": str(comp.get("SUMMARY") or "Busy"),
        "start": start,
        "end": end,
    }


def _parse_search_results(events: Sequence[Any], calendar_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    skipped = 0
    for ev in events:
        row = _vevent_to_dict(ev)
        if row is None:
            skipped += 1
            continue
        out.append(row)
    if skipped:
        logger.warning(
            "CalDAV: calendar %r skipped %d/%d events (missing DTSTART or parse error)",
            calendar_name,
            skipped,
            len(events),
        )
    return out


def list_caldav_calendars(
    *,
    url: str,
    username: str,
    password: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Return each calendar's display name and event count in ``[start, end]`` (for operator debugging)."""
    caldav = _import_caldav()
    calendars = caldav.DAVClient(url=url, username=username, password=password).principal().calendars()
    rows: list[dict[str, Any]] = []
    for cal in calendars:
        name = _calendar_display_name(cal)
        try:
            count = len(cal.search(start=start, end=end, event=True, expand=True))
        except Exception as exc:
            logger.warning("CalDAV: search failed for %r: %s", name, exc)
            count = -1
        rows.append({"name": name, "event_count": count})
    return rows


def fetch_caldav_events(
    *,
    url: str,
    username: str,
    password: str,
    start: date,
    end: date,
    calendar_name: str | None = None,
) -> list[dict[str, Any]]:
    """Poll CalDAV calendars in ``[start, end]``; optional name filter (case-insensitive, substring)."""
    caldav = _import_caldav()
    calendars = caldav.DAVClient(url=url, username=username, password=password).principal().calendars()
    if not calendars:
        logger.info("CalDAV: no calendars on principal")
        return []

    out: list[dict[str, Any]] = []
    matched = 0
    for cal in calendars:
        name = _calendar_display_name(cal)
        if not _calendar_matches(cal, calendar_name):
            continue
        matched += 1
        try:
            events = cal.search(start=start, end=end, event=True, expand=True)
        except Exception as exc:
            logger.warning("CalDAV: search failed for %r: %s", name, exc)
            continue
        parsed = _parse_search_results(events, name)
        logger.info("CalDAV: calendar %r events=%d parsed=%d", name, len(events), len(parsed))
        out.extend(parsed)

    if calendar_name and matched == 0:
        available = [_calendar_display_name(c) for c in calendars]
        logger.warning(
            "CalDAV: no calendar matched filter %r; available: %s",
            calendar_name,
            available,
        )

    return out

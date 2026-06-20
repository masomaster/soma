"""Phase 7 CalDAV calendar → interventions adapter tests."""

from __future__ import annotations

from datetime import date

from pipeline.adapters.caldav_calendar import (
    CALDAV_SOURCE,
    _calendar_matches,
    _vevent_to_dict,
    normalize_caldav_events,
)

_USER = "00000000-0000-0000-0000-000000000001"


def test_normalize_caldav_events_maps_busy_blocks():
    events = [
        {
            "summary": "Team standup",
            "start": date(2024, 6, 1),
            "end": date(2024, 6, 1),
        }
    ]
    rows = normalize_caldav_events(events, _USER)
    assert len(rows) == 1
    r = rows[0]
    assert r["user_id"] == _USER
    assert r["event_date"] == date(2024, 6, 1)
    assert r["category"] == "calendar_busy"
    assert "Team standup" in r["description"]
    assert r["notes"] == CALDAV_SOURCE


def test_calendar_matches_name_filter_case_insensitive():
    class _Cal:
        def __init__(self, name: str) -> None:
            self.name = name

    assert _calendar_matches(_Cal("Mason"), "mason") is True
    assert _calendar_matches(_Cal("Mason Work Calendar"), "Mason") is True
    assert _calendar_matches(_Cal("Caroline"), "Mason") is False
    assert _calendar_matches(_Cal("Work"), None) is True


def test_vevent_to_dict_from_icalendar_component():
    from datetime import datetime

    from icalendar import Event

    ev_comp = Event()
    ev_comp.add("SUMMARY", "Team standup")
    ev_comp.add("DTSTART", datetime(2026, 6, 18, 10, 0))
    ev_comp.add("DTEND", datetime(2026, 6, 18, 11, 0))

    class _FakeCalDavEvent:
        def get_icalendar_component(self):
            return ev_comp

    row = _vevent_to_dict(_FakeCalDavEvent())
    assert row is not None
    assert row["summary"] == "Team standup"
    assert row["start"] == datetime(2026, 6, 18, 10, 0)
    assert row["end"] == datetime(2026, 6, 18, 11, 0)

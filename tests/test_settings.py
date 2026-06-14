"""Settings helpers (environment, optional briefing URL)."""

from __future__ import annotations

import pytest

from pipeline.settings import get_briefing_email_dashboard_url


def test_get_briefing_email_dashboard_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("BRIEFING_EMAIL_DASHBOARD_URL", raising=False)
    assert get_briefing_email_dashboard_url() is None


def test_get_briefing_email_dashboard_url_accepts_http_https(monkeypatch):
    monkeypatch.setenv("BRIEFING_EMAIL_DASHBOARD_URL", "https://dash.example/path")
    assert get_briefing_email_dashboard_url() == "https://dash.example/path"


def test_get_briefing_email_dashboard_url_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("BRIEFING_EMAIL_DASHBOARD_URL", "javascript:alert(1)")
    assert get_briefing_email_dashboard_url() is None

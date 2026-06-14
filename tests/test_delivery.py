"""Phase 6 delivery tests (stdout for local, injected SES otherwise)."""

from __future__ import annotations

import io
from datetime import date

from pipeline import delivery as D
from pipeline.briefing import Briefing
from pipeline.settings import Environment

RUN = date(2024, 6, 8)


def _briefing() -> Briefing:
    return Briefing(
        user_id="u1",
        briefing_date=RUN,
        coaching_note="Easy day.",
        flags=["LOW_HRV"],
        features_json={},
        model_used="claude-haiku-4-5-20251001",
    )


def test_local_env_prints_to_stream():
    buf = io.StringIO()
    out = D.deliver_briefing(_briefing(), env=Environment.LOCAL, stream=buf)
    assert out["channel"] == "stdout"
    text = buf.getvalue()
    assert "[LOCAL] Soma briefing — 2024-06-08" in text
    assert "Easy day." in text
    assert "LOW_HRV" in text


def test_prod_env_sends_email_without_prefix():
    sent: dict[str, str | None] = {}

    def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> str:
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = body
        sent["html"] = html_body
        return "msg-123"

    out = D.deliver_briefing(
        _briefing(),
        to_address="user@example.com",
        env=Environment.PROD,
        send_email=send_email,
    )
    assert out == {
        "channel": "email",
        "subject": "Soma briefing — 2024-06-08",
        "message_id": "msg-123",
    }
    assert sent["to"] == "user@example.com"
    assert not sent["subject"].startswith("[")
    assert sent["body"] == "Easy day."
    assert isinstance(sent["html"], str) and "Easy day." in sent["html"] and "<html" in sent["html"]


def test_prod_email_html_includes_dashboard_when_env_set(monkeypatch):
    monkeypatch.setenv("BRIEFING_EMAIL_DASHBOARD_URL", "https://dash.example/home")
    sent: dict[str, str | None] = {}

    def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> str:
        sent["html"] = html_body
        return "id"

    D.deliver_briefing(
        _briefing(),
        to_address="user@example.com",
        env=Environment.PROD,
        send_email=send_email,
    )
    assert sent["html"] is not None
    assert "Open your dashboard" in sent["html"]
    assert "https://dash.example/home" in sent["html"]


def test_coaching_note_to_html_wraps_bold_and_headings():
    html = D.coaching_note_to_html("# Title\n\n**Bold** line\n\n- one\n- two")
    assert 'lang="en"' in html
    assert "<h1" in html and "Title" in html
    assert "<strong>Bold</strong>" in html
    assert "<ul" in html and "<li>one</li>" in html
    assert "Soma</p>" in html or "Soma" in html


def test_coaching_note_to_html_optional_dashboard_link():
    html = D.coaching_note_to_html("**Hi**", dashboard_url="https://dash.example/home")
    assert "Open your dashboard" in html
    assert "https://dash.example/home" in html
    assert 'href="https://dash.example/home"' in html


def test_staging_without_address_falls_back_to_stdout():
    buf = io.StringIO()
    out = D.deliver_briefing(
        _briefing(),
        env=Environment.STAGING,
        send_email=lambda *a: "x",
        stream=buf,
    )
    assert out["channel"] == "stdout"
    assert "[STAGING]" in buf.getvalue()

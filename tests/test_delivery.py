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
        model_used="claude-3-5-haiku-latest",
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
    sent = {}

    def send_email(to: str, subject: str, body: str) -> str:
        sent.update(to=to, subject=subject, body=body)
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

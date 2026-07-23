"""Tests for the alarm-notify Lambda handler (SNS → SES)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


def _load_handler_mod():
    root = Path(__file__).resolve().parents[1]
    path = root / "infrastructure" / "lambda" / "alarm_notify" / "handler.py"
    spec = importlib.util.spec_from_file_location("soma_alarm_notify_handler", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_handler_sends_ses_for_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    handler_mod = _load_handler_mod()
    sent: list[tuple[Any, ...]] = []

    def fake_sender(sender: str, *, region: str | None = None, client: Any = None):
        def _send(to: str, subject: str, body: str, html_body: str | None = None) -> str:
            sent.append((sender, to, subject, body))
            return "mid-1"

        return _send

    monkeypatch.setenv("ALARM_TO_EMAIL", "ops@example.com")
    monkeypatch.setenv("SES_SENDER", "soma@example.com")
    monkeypatch.setattr(handler_mod, "ses_email_sender", fake_sender)

    event = {
        "Records": [
            {
                "Sns": {
                    "Message": json.dumps(
                        {
                            "AlarmName": "soma-hevy-ingest-lambda-errors",
                            "NewStateValue": "ALARM",
                            "OldStateValue": "OK",
                            "NewStateReason": "errors >= 1",
                        }
                    )
                }
            }
        ]
    }
    out = handler_mod.handler(event, None)
    assert out == {"ok": True, "sent": 1, "skipped": 0}
    assert sent[0][0] == "soma@example.com"
    assert sent[0][1] == "ops@example.com"
    assert sent[0][2].startswith("[Soma ALARM]")


def test_handler_skips_insufficient_data(monkeypatch: pytest.MonkeyPatch) -> None:
    handler_mod = _load_handler_mod()
    monkeypatch.setenv("ALARM_TO_EMAIL", "ops@example.com")
    monkeypatch.setenv("SES_SENDER", "soma@example.com")

    def fake_sender(sender: str, *, region: str | None = None, client: Any = None):
        def _send(*_a: Any, **_k: Any) -> str:
            raise AssertionError("should not send")

        return _send

    monkeypatch.setattr(handler_mod, "ses_email_sender", fake_sender)
    event = {
        "Records": [
            {
                "Sns": {
                    "Message": json.dumps(
                        {
                            "AlarmName": "soma-x",
                            "NewStateValue": "INSUFFICIENT_DATA",
                        }
                    )
                }
            }
        ]
    }
    out = handler_mod.handler(event, None)
    assert out["sent"] == 0
    assert out["skipped"] == 1

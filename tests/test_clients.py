"""Tests for concrete IO clients (Anthropic via injected urlopen; SES/SSM fakes)."""

from __future__ import annotations

import json

import pytest

from pipeline import clients


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_anthropic_llm_posts_and_parses_text():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}]})

    llm = clients.anthropic_llm("sk-test", model="claude-3-5-haiku-latest", urlopen=fake_urlopen)
    out = llm("SYS", "USER PROMPT")

    assert out == "Hello world"
    assert captured["url"] == clients.ANTHROPIC_URL
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == clients.ANTHROPIC_VERSION
    assert captured["body"]["system"] == "SYS"
    assert captured["body"]["messages"][0]["content"] == "USER PROMPT"


def test_anthropic_llm_raises_on_empty_content():
    llm = clients.anthropic_llm("k", model="m", urlopen=lambda req, timeout=None: _FakeResp({"content": []}))
    with pytest.raises(ValueError, match="content"):
        llm("s", "u")


def test_ses_email_sender_uses_injected_client():
    calls = {}

    class FakeSes:
        def send_email(self, **kwargs):
            calls.update(kwargs)
            return {"MessageId": "m-1"}

    send = clients.ses_email_sender("from@soma.app", client=FakeSes())
    mid = send("to@x.com", "Subj", "Body")
    assert mid == "m-1"
    assert calls["Source"] == "from@soma.app"
    assert calls["Destination"]["ToAddresses"] == ["to@x.com"]
    assert calls["Message"]["Subject"]["Data"] == "Subj"


def test_ssm_threshold_loader_flattens_pages():
    class FakePaginator:
        def paginate(self, **kwargs):
            assert kwargs["Path"] == "/soma/staging/u1/rules/"
            yield {"Parameters": [{"Name": "/soma/staging/u1/rules/min_sleep_hours", "Value": "6"}]}
            yield {"Parameters": [{"Name": "/soma/staging/u1/rules/target_sleep_hours", "Value": "8"}]}

    class FakeSsm:
        def get_paginator(self, name):
            assert name == "get_parameters_by_path"
            return FakePaginator()

    get = clients.ssm_threshold_loader(client=FakeSsm())
    out = get("/soma/staging/u1/rules/")
    assert out == {
        "/soma/staging/u1/rules/min_sleep_hours": "6",
        "/soma/staging/u1/rules/target_sleep_hours": "8",
    }

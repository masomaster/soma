"""Smoke tests for the Apple Health ingest Lambda handler (outside ``pipeline`` package)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_handler():
    root = Path(__file__).resolve().parents[1]
    path = root / "infrastructure" / "lambda" / "apple_health_webhook" / "handler.py"
    spec = importlib.util.spec_from_file_location("apple_health_webhook_handler", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


def test_handler_rejects_invalid_x_soma_user_id() -> None:
    handler = _load_handler()
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"content-type": "application/json", "x-soma-user-id": "not-a-uuid"},
        "body": "{}",
        "isBase64Encoded": False,
    }
    out = handler(event, None)
    assert out["statusCode"] == 400
    body = json.loads(out["body"])
    assert body["error"] == "invalid_x_soma_user_id"
    assert "hint" in body

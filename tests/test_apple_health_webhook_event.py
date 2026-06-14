"""Tests for API Gateway event parsing used by the Apple Health webhook."""

from __future__ import annotations

import json

from pipeline.apple_health_webhook_event import (
    header_first,
    merge_api_gateway_headers,
    parse_json_body,
    raw_body_bytes,
)


def test_merge_api_gateway_headers_prefers_primary() -> None:
    ev = {
        "headers": {"X-Soma-User-Id": "from-headers"},
        "multiValueHeaders": {"X-Soma-User-Id": ["from-mv"]},
    }
    m = merge_api_gateway_headers(ev)
    assert header_first(m, "x-soma-user-id") == "from-headers"


def test_header_first_accepts_list_value() -> None:
    assert header_first({"x-soma-user-id": ["00000000-0000-0000-0000-000000000001"]}, "X-Soma-User-Id") == (
        "00000000-0000-0000-0000-000000000001"
    )


def test_raw_body_dict_serializes() -> None:
    b = raw_body_bytes({"body": {"data": {"metrics": []}}, "isBase64Encoded": False})
    assert json.loads(b.decode()) == {"data": {"metrics": []}}


def test_raw_body_base64() -> None:
    import base64

    s = '{"a":1}'
    b64 = base64.b64encode(s.encode()).decode("ascii")
    out = raw_body_bytes({"body": b64, "isBase64Encoded": True})
    assert out == s.encode()


def test_parse_json_body_bom() -> None:
    raw = "\ufeff{}".encode("utf-8")
    obj, err = parse_json_body(raw)
    assert err is None
    assert obj == {}


def test_parse_json_body_empty() -> None:
    assert parse_json_body(b"  ") == (None, "empty_body")

"""API Gateway (HTTP API) → Lambda event helpers for the Apple Health webhook."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Short hints returned in JSON and logs for 400 responses (Health Auto Export UX).
HINT_MISSING_USER = (
    "Add HTTP header X-Soma-User-Id with your Supabase auth.users id (UUID). "
    "In Health Auto Export custom headers: Key = X-Soma-User-Id, Value = <uuid>."
)
HINT_INVALID_USER_ID = (
    "X-Soma-User-Id must be a valid UUID (your Supabase auth.users id). "
    "Copy the UUID exactly; no extra spaces or characters."
)
HINT_EMPTY_BODY = (
    "POST body was empty. Use POST with JSON body (HAE export format). "
    "Confirm the automation sends a body (not only headers)."
)
HINT_INVALID_JSON = (
    "Body must be UTF-8 JSON, typically {\"data\":{\"metrics\":[...],\"workouts\":[...]}}."
)


def _coerce_header_value(value: Any) -> str | None:
    """Normalize API Gateway header values (string or single-element list)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def merge_api_gateway_headers(event: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``headers`` and ``multiValueHeaders`` (REST/ALB-style) into one map."""
    out: dict[str, Any] = {}
    h = event.get("headers")
    if isinstance(h, dict):
        out.update(h)
    mv = event.get("multiValueHeaders")
    if isinstance(mv, dict):
        for k, vals in mv.items():
            lk = str(k).lower()
            found_key = None
            for ok in out:
                if str(ok).lower() == lk:
                    found_key = ok
                    break
            if found_key is not None and _coerce_header_value(out.get(found_key)):
                continue
            if isinstance(vals, list) and vals:
                out[k] = vals[0]
            elif isinstance(vals, str):
                out[k] = vals
    return out


def canonical_auth_user_uuid(raw: str) -> str | None:
    """Return normalized UUID string for ``auth.users.id``, or ``None`` if invalid."""
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        return None


def header_first(headers: Mapping[str, Any] | None, name: str) -> str | None:
    """Case-insensitive header lookup with list/tuple value support."""
    if not isinstance(headers, dict):
        return None
    lower = {str(k).lower(): v for k, v in headers.items()}
    return _coerce_header_value(lower.get(name.lower()))


def raw_body_bytes(event: Mapping[str, Any]) -> bytes:
    """Return request body as bytes (empty if missing).

    API Gateway normally sends ``body`` as a ``str``; some proxies use ``dict`` or
    ``bytes`` — we normalize so the webhook does not return **empty_body** falsely.
    """
    raw = event.get("body")
    if raw is None:
        return b""
    if isinstance(raw, dict):
        return json.dumps(raw, separators=(",", ":"), default=str).encode("utf-8")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if not isinstance(raw, str):
        logger.warning("Unexpected event.body type %s; treating as empty", type(raw).__name__)
        return b""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8")


def parse_json_body(raw_bytes: bytes) -> tuple[Any, str | None]:
    """Parse JSON from UTF-8 bytes. Returns ``(obj, None)`` or ``(None, error_code)``."""
    if not raw_bytes.strip():
        return None, "empty_body"
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "invalid_utf8"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "invalid_json"

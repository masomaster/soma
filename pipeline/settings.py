"""Runtime environment from ``ENV`` (local, staging, prod)."""

from __future__ import annotations

import logging
import os
from enum import StrEnum

logger = logging.getLogger(__name__)


class Environment(StrEnum):
    """Deployment / development environment names."""

    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


def get_environment() -> Environment:
    """Return the current ``Environment`` from ``ENV`` (default: ``local``).

    Raises:
        ValueError: If ``ENV`` is set to an unrecognized value.
    """
    raw = os.environ.get("ENV", "local").strip().lower()
    try:
        return Environment(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid ENV: {raw!r}; expected one of "
            f"{', '.join(repr(m.value) for m in Environment)}"
        ) from exc


def get_briefing_email_dashboard_url() -> str | None:
    """Optional HTTPS (or HTTP) link appended to briefing HTML emails (Phase 6.6).

    Set ``BRIEFING_EMAIL_DASHBOARD_URL`` to a full URL. Other schemes are rejected
    so the href cannot be abused as ``javascript:...``.
    """
    raw = os.environ.get("BRIEFING_EMAIL_DASHBOARD_URL", "").strip()
    if not raw:
        return None
    if not (raw.startswith("https://") or raw.startswith("http://")):
        logger.warning(
            "Ignoring BRIEFING_EMAIL_DASHBOARD_URL: must start with http:// or https://"
        )
        return None
    return raw

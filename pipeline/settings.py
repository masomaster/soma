"""Runtime environment from ``ENV`` (local, staging, prod)."""

from __future__ import annotations

import os
from enum import StrEnum


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

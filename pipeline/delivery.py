"""Phase 6 briefing delivery: stdout when ``ENV=local``, email (SES) otherwise.

Per ``.cursor/rules/soma.mdc``: when ``ENV=local`` the briefing is printed to
stdout instead of sending email. The email sender is injected (wraps SES
``send_email`` in Lambda) so this module needs no AWS dependency to test.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, TextIO

from pipeline.briefing import Briefing
from pipeline.settings import Environment, get_environment

logger = logging.getLogger(__name__)

# Injected email sender: (to_address, subject, body) -> provider message id.
EmailSender = Callable[[str, str, str], str]


def _subject(briefing: Briefing, env: Environment) -> str:
    prefix = "" if env is Environment.PROD else f"[{env.value.upper()}] "
    return f"{prefix}Soma briefing — {briefing.briefing_date.isoformat()}"


def deliver_briefing(
    briefing: Briefing,
    *,
    to_address: str | None = None,
    env: Environment | None = None,
    send_email: EmailSender | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Deliver ``briefing`` and return a small result dict describing what happened.

    - ``ENV=local`` (or no ``send_email`` / ``to_address``): print to ``stream``
      (default stdout) and return ``{"channel": "stdout", ...}``.
    - Otherwise: call ``send_email(to_address, subject, body)`` and return
      ``{"channel": "email", "message_id": ...}``.
    """
    resolved_env = env or get_environment()
    subject = _subject(briefing, resolved_env)

    can_email = (
        resolved_env is not Environment.LOCAL and send_email is not None and bool(to_address)
    )
    if not can_email:
        out = stream or sys.stdout
        print(f"=== {subject} ===", file=out)
        print(briefing.coaching_note, file=out)
        if briefing.flags:
            print(f"(flags: {', '.join(briefing.flags)})", file=out)
        return {"channel": "stdout", "subject": subject}

    assert send_email is not None and to_address is not None  # narrowed by can_email
    message_id = send_email(to_address, subject, briefing.coaching_note)
    logger.info("Sent briefing for %s via email (message_id=%s)", briefing.user_id, message_id)
    return {"channel": "email", "subject": subject, "message_id": message_id}

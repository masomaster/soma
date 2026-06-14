"""Phase 6 briefing delivery: stdout when ``ENV=local``, email (SES) otherwise.

Per ``.cursor/rules/soma.mdc``: when ``ENV=local`` the briefing is printed to
stdout instead of sending email. The email sender is injected (wraps SES
``send_email`` in Lambda) so this module needs no AWS dependency to test.
"""

from __future__ import annotations

import html
import logging
import re
import sys
from typing import Any, Protocol, TextIO

from pipeline.briefing import Briefing
from pipeline.settings import Environment, get_environment

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    """SES (or test double): plain-text body plus optional HTML part."""

    def __call__(
        self, to_address: str, subject: str, body: str, html_body: str | None = None
    ) -> str: ...


def _inline_emphasis(text: str) -> str:
    """Escape HTML and wrap ``**segments**`` in ``<strong>``."""
    parts = re.split(r"(\*\*.+?\*\*)", text)
    out: list[str] = []
    for part in parts:
        if len(part) >= 4 and part.startswith("**") and part.endswith("**"):
            inner = html.escape(part[2:-2])
            out.append(f"<strong>{inner}</strong>")
        else:
            out.append(html.escape(part))
    return "".join(out)


def coaching_note_to_html(note: str) -> str:
    """Convert a short Markdown-ish coaching note into a minimal HTML body for SES."""
    text = note.replace("\r\n", "\n").strip()
    if not text:
        return (
            '<!DOCTYPE html><html><body style="font-family:system-ui,Segoe UI,sans-serif">'
            "</body></html>"
        )
    blocks = re.split(r"\n\n+", text)
    chunks: list[str] = [
        '<!DOCTYPE html><html><body style="font-family:system-ui,Segoe UI,sans-serif;'
        'max-width:36rem;line-height:1.45">'
    ]
    for raw in blocks:
        block = raw.strip()
        if not block:
            continue
        lines = block.split("\n")
        if len(lines) == 1 and block.startswith("## "):
            chunks.append(
                f'<h2 style="font-size:1.05rem;margin:1rem 0 0.5rem">{_inline_emphasis(block[3:].strip())}</h2>'
            )
        elif len(lines) == 1 and block.startswith("# "):
            chunks.append(
                f'<h1 style="font-size:1.25rem;margin:0 0 0.75rem">{_inline_emphasis(block[2:].strip())}</h1>'
            )
        elif lines and all(not ln.strip() or ln.strip().startswith("- ") for ln in lines):
            items: list[str] = []
            for ln in lines:
                s = ln.strip()
                if not s or not s.startswith("- "):
                    continue
                items.append(f"<li>{_inline_emphasis(s[2:].strip())}</li>")
            chunks.append(
                f'<ul style="margin:0 0 0.75rem 1.1rem;padding:0">{"".join(items)}</ul>'
            )
        else:
            inner = "<br/>".join(_inline_emphasis(ln) for ln in lines)
            chunks.append(f'<p style="margin:0 0 0.75rem">{inner}</p>')
    chunks.append("</body></html>")
    return "".join(chunks)


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
    - Otherwise: call ``send_email(to_address, subject, body, html_body=...)`` and return
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
    html_body = coaching_note_to_html(briefing.coaching_note)
    message_id = send_email(
        to_address, subject, briefing.coaching_note, html_body=html_body
    )
    logger.info("Sent briefing for %s via email (message_id=%s)", briefing.user_id, message_id)
    return {"channel": "email", "subject": subject, "message_id": message_id}

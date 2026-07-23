"""Format CloudWatch alarm SNS notifications into operator email content.

CloudWatch publishes a JSON document on the SNS topic when an alarm changes
state. Soma delivers that via SES (Lambda subscriber) so operators do not need
to confirm an SNS email subscription.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_cloudwatch_alarm_message(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Parse the SNS ``Message`` body (JSON string or already-decoded dict)."""
    if isinstance(raw, dict):
        return raw
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("CloudWatch alarm message must be a JSON object")
    return data


def format_alarm_email(alarm: dict[str, Any]) -> tuple[str, str]:
    """Return ``(subject, plain_text_body)`` for an operator inbox."""
    name = str(alarm.get("AlarmName") or "unknown-alarm")
    state = str(alarm.get("NewStateValue") or "UNKNOWN")
    old = str(alarm.get("OldStateValue") or "")
    reason = str(alarm.get("NewStateReason") or "").strip()
    description = str(alarm.get("AlarmDescription") or "").strip()
    region = str(alarm.get("Region") or alarm.get("AWSAccountId") or "").strip()
    when = str(alarm.get("StateChangeTime") or "").strip()

    subject = f"[Soma {state}] {name}"
    lines = [
        f"Alarm: {name}",
        f"State: {old} → {state}" if old else f"State: {state}",
    ]
    if when:
        lines.append(f"Time: {when}")
    if region:
        lines.append(f"Region: {region}")
    if description:
        lines.append("")
        lines.append(description)
    if reason:
        lines.append("")
        lines.append(f"Reason: {reason}")
    lines.extend(
        [
            "",
            "CloudWatch → Alarms (filter by name above) for metric history.",
            "Lambda logs: /aws/lambda/<function> matching the alarm name prefix.",
        ]
    )
    return subject, "\n".join(lines)


def iter_sns_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract SNS records from a Lambda event (empty if none)."""
    records = event.get("Records")
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        sns = rec.get("Sns")
        if isinstance(sns, dict):
            out.append(sns)
    return out

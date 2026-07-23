"""SNS → SES bridge for Soma pipeline CloudWatch alarms.

CloudWatch alarms publish to ``soma-daily-pipeline-alarms``. This Lambda
subscribes to that topic and emails the operator via SES (already verified for
daily briefings). Unlike SNS email subscriptions, no Confirm-subscription click
is required.

Environment:

    ALARM_TO_EMAIL            Operator inbox (CDK context ``soma:pipelineAlarmEmail``)
    SES_SENDER                Verified From address (optional if secret is set)
    SOMA_BRIEFING_SECRET_ARN  JSON with ``SES_SENDER`` (same secret as briefing)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pipeline.alarm_notify import (
    format_alarm_email,
    iter_sns_records,
    parse_cloudwatch_alarm_message,
)
from pipeline.clients import ses_email_sender
from pipeline.lambda_secrets import resolve_ses_sender

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    to_address = os.environ.get("ALARM_TO_EMAIL", "").strip()
    if not to_address:
        raise OSError("ALARM_TO_EMAIL is not configured")

    sender = resolve_ses_sender()
    send = ses_email_sender(sender)
    sent = 0
    skipped = 0

    for sns in iter_sns_records(event or {}):
        raw = sns.get("Message", "")
        try:
            alarm = parse_cloudwatch_alarm_message(
                raw if isinstance(raw, (str, dict)) else str(raw)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.exception("Skipping unparseable alarm SNS message: %s", exc)
            skipped += 1
            continue
        state = str(alarm.get("NewStateValue") or "")
        if state == "INSUFFICIENT_DATA":
            logger.info("Skipping INSUFFICIENT_DATA for %s", alarm.get("AlarmName"))
            skipped += 1
            continue
        subject, body = format_alarm_email(alarm)
        message_id = send(to_address, subject, body)
        logger.info(
            "Alarm email sent alarm=%s state=%s message_id=%s",
            alarm.get("AlarmName"),
            state,
            message_id,
        )
        sent += 1

    return {"ok": True, "sent": sent, "skipped": skipped}

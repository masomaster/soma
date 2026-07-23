"""Unit tests for CloudWatch alarm → email formatting."""

from __future__ import annotations

import json

from pipeline.alarm_notify import (
    format_alarm_email,
    iter_sns_records,
    parse_cloudwatch_alarm_message,
)


def test_parse_and_format_alarm_email() -> None:
    payload = {
        "AlarmName": "soma-caldav-ingest-lambda-errors",
        "AlarmDescription": "CalDAV Lambda Errors",
        "NewStateValue": "ALARM",
        "OldStateValue": "OK",
        "NewStateReason": "Threshold Crossed: 1 datapoint [1.0]",
        "StateChangeTime": "2026-07-23T08:05:00.000+0000",
        "Region": "US West (Oregon)",
    }
    alarm = parse_cloudwatch_alarm_message(json.dumps(payload))
    subject, body = format_alarm_email(alarm)
    assert subject == "[Soma ALARM] soma-caldav-ingest-lambda-errors"
    assert "OK → ALARM" in body
    assert "CalDAV Lambda Errors" in body
    assert "Threshold Crossed" in body


def test_iter_sns_records() -> None:
    event = {
        "Records": [
            {"Sns": {"Message": '{"AlarmName":"a","NewStateValue":"ALARM"}'}},
            {"eventSource": "aws:sns"},
        ]
    }
    recs = iter_sns_records(event)
    assert len(recs) == 1
    assert parse_cloudwatch_alarm_message(recs[0]["Message"])["AlarmName"] == "a"

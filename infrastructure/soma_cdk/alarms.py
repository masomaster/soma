"""Shared CloudWatch alarm → SNS wiring for Soma pipelines."""

from __future__ import annotations

from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns


def wire_pipeline_alarm(alarm: cloudwatch.Alarm, topic: sns.ITopic) -> None:
    """Notify on ALARM and OK transitions (SNS → SES notifier Lambda)."""
    action = cw_actions.SnsAction(topic)
    alarm.add_alarm_action(action)
    alarm.add_ok_action(action)

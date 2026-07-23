"""EventBridge Scheduler → weekly signal job (patterns + optional Sonnet)."""

from __future__ import annotations

import os

from aws_cdk import Duration, TimeZone
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_sns as sns
from constructs import Construct

from soma_cdk.alarms import wire_pipeline_alarm
from soma_cdk.config import DEPLOYED_ENV
from soma_cdk.runtime_secrets import RuntimeSecrets

_WEEKLY_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "weekly_signal")


class WeeklySignalPipeline(Construct):
    """Sunday UTC cron → recompute ``metric_patterns`` + optional LLM pattern rows."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        deps_layer: lambda_.ILayerVersion,
        runtime_secrets: RuntimeSecrets,
        schedule_hour_utc: int = 12,
        schedule_enabled: bool = True,
        pipeline_alarm_topic: sns.ITopic | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        base_name = "soma-weekly-signal"
        env_vars = {
            "ENV": DEPLOYED_ENV,
            **runtime_secrets.env_weekly_signal(),
        }
        if extra_env:
            env_vars.update(extra_env)

        fn = lambda_.Function(
            self,
            "WeeklySignalFn",
            function_name=base_name,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_WEEKLY_ASSET),
            layers=[deps_layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment=env_vars,
        )
        runtime_secrets.grant_weekly_signal(fn)

        schedule_name = base_name
        self.schedule: scheduler.Schedule | None = None
        if schedule_enabled:
            self.schedule = scheduler.Schedule(
                self,
                "WeeklySignalCron",
                schedule_name=schedule_name,
                schedule=scheduler.ScheduleExpression.cron(
                    minute="0",
                    hour=str(schedule_hour_utc),
                    week_day="SUN",
                    time_zone=TimeZone.ETC_UTC,
                ),
                target=scheduler_targets.LambdaInvoke(fn),
                description="Weekly metric patterns + optional Sonnet scan",
            )
        self.function = fn

        if pipeline_alarm_topic is not None:
            topic = pipeline_alarm_topic
            wire_pipeline_alarm(
                cloudwatch.Alarm(
                    self,
                    "WeeklyLambdaErrors",
                    alarm_name=f"{base_name}-lambda-errors",
                    metric=fn.metric_errors(
                        statistic=cloudwatch.Stats.SUM, period=Duration.minutes(5)
                    ),
                    threshold=1,
                    evaluation_periods=1,
                    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                ),
                topic,
            )
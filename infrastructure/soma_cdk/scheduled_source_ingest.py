"""Reusable EventBridge Scheduler → Lambda ingest construct (Phase 7)."""

from __future__ import annotations

import os
from typing import Literal

from aws_cdk import Duration, TimeZone
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_sns as sns
from constructs import Construct

from soma_cdk.alarms import wire_pipeline_alarm
from soma_cdk.config import DEPLOYED_ENV
from soma_cdk.runtime_secrets import RuntimeSecrets

_SecretProfile = Literal["caldav", "strava", "dropbox"]


class ScheduledSourceIngest(Construct):
    """Scheduler + Lambda for a single source ingest handler asset."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        source_slug: str,
        handler_asset_subdir: str,
        deps_layer: lambda_.ILayerVersion,
        runtime_secrets: RuntimeSecrets,
        secret_profile: _SecretProfile,
        raw_bucket: s3.IBucket,
        schedule_hour_utc: int,
        schedule_minute_utc: int = 0,
        schedule_enabled: bool = True,
        timeout_minutes: int = 5,
        memory_mb: int = 512,
        pipeline_alarm_topic: sns.ITopic | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        asset_path = os.path.join(
            os.path.dirname(__file__), "..", "lambda", handler_asset_subdir
        )
        base_name = f"soma-{source_slug}-ingest"
        if secret_profile == "caldav":
            secret_env = runtime_secrets.env_caldav()
            grant_fn = runtime_secrets.grant_caldav
        elif secret_profile == "strava":
            secret_env = runtime_secrets.env_strava()
            grant_fn = runtime_secrets.grant_strava
        elif secret_profile == "dropbox":
            secret_env = runtime_secrets.env_dropbox()
            grant_fn = runtime_secrets.grant_dropbox
        else:
            raise ValueError(f"Unsupported secret_profile: {secret_profile!r}")

        env_vars = {
            "ENV": DEPLOYED_ENV,
            "RAW_BUCKET": raw_bucket.bucket_name,
            **secret_env,
        }
        if extra_env:
            env_vars.update(extra_env)

        fn = lambda_.Function(
            self,
            "IngestFn",
            function_name=base_name,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(asset_path),
            layers=[deps_layer],
            timeout=Duration.minutes(timeout_minutes),
            memory_size=memory_mb,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment=env_vars,
        )
        raw_bucket.grant_put(fn)
        grant_fn(fn)

        schedule_name = base_name
        self.schedule: scheduler.Schedule | None = None
        if schedule_enabled:
            self.schedule = scheduler.Schedule(
                self,
                "SchedulerCron",
                schedule_name=schedule_name,
                schedule=scheduler.ScheduleExpression.cron(
                    minute=str(schedule_minute_utc),
                    hour=str(schedule_hour_utc),
                    time_zone=TimeZone.ETC_UTC,
                ),
                target=scheduler_targets.LambdaInvoke(fn),
                description=f"{source_slug} scheduled ingest",
            )

        self.function = fn

        if pipeline_alarm_topic is not None:
            topic = pipeline_alarm_topic
            sched_dims = {"ScheduleGroup": "default", "ScheduleName": schedule_name}
            if schedule_enabled:
                wire_pipeline_alarm(
                    cloudwatch.Alarm(
                        self,
                        "SchedulerTargetErrors",
                        alarm_name=f"{base_name}-scheduler-target-errors",
                        metric=cloudwatch.Metric(
                            namespace="AWS/Scheduler",
                            metric_name="TargetErrorCount",
                            dimensions_map=sched_dims,
                            statistic=cloudwatch.Stats.SUM,
                            period=Duration.minutes(5),
                        ),
                        threshold=1,
                        evaluation_periods=1,
                        comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                        treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                    ),
                    topic,
                )
            wire_pipeline_alarm(
                cloudwatch.Alarm(
                    self,
                    "LambdaErrors",
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
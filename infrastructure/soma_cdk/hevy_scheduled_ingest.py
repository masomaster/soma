"""EventBridge **Scheduler** → Lambda: Hevy API → raw S3 → ``strength_events``.

Uses the **same** raw bucket as :class:`soma_cdk.apple_health_ingest.AppleHealthIngestApi`
and shared secrets ``soma-db``, ``soma-hevy``, ``soma-tenant``.
Schedule defaults to **09:00 UTC**, before :class:`soma_cdk.daily_pipeline.DailyBriefingPipeline`
(11:00 UTC) so strength data is fresh for the daily pipeline.
"""

from __future__ import annotations

import os

from aws_cdk import Duration, TimeZone
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_sns as sns
from constructs import Construct

from soma_cdk.runtime_secrets import RuntimeSecrets

_HEVY_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "hevy_ingest")


class HevyScheduledIngest(Construct):
    """S3 raw bucket + Scheduler + Lambda for Hevy pulls.

    When ``pipeline_alarm_topic`` is set (same topic as ``DailyBriefingPipeline``),
    adds Scheduler + Lambda error alarms so ingest failures page like the briefing.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        deps_layer: lambda_.ILayerVersion,
        runtime_secrets: RuntimeSecrets,
        raw_bucket: s3.IBucket,
        schedule_hour_utc: int = 9,
        schedule_enabled: bool = True,
        pipeline_alarm_topic: sns.ITopic | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        fn = lambda_.Function(
            self,
            "HevyIngestFn",
            function_name=f"soma-{env_name}-hevy-ingest",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_HEVY_ASSET),
            layers=[deps_layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "ENV": env_name,
                "RAW_BUCKET": raw_bucket.bucket_name,
                **runtime_secrets.env_hevy(),
            },
        )
        raw_bucket.grant_put(fn)
        runtime_secrets.grant_hevy(fn)

        schedule_name = f"soma-{env_name}-hevy-ingest"
        self.schedule: scheduler.Schedule | None = None
        if schedule_enabled:
            # Id ``SchedulerHevyCron`` (not ``HevyIngestSchedule``): new CFN logical id so Rule→Schedule is not an in-place type swap.
            self.schedule = scheduler.Schedule(
                self,
                "SchedulerHevyCron",
                schedule_name=schedule_name,
                schedule=scheduler.ScheduleExpression.cron(
                    minute="0",
                    hour=str(schedule_hour_utc),
                    time_zone=TimeZone.ETC_UTC,
                ),
                target=scheduler_targets.LambdaInvoke(fn),
                description="Hevy scheduled ingest (EventBridge Scheduler → Lambda)",
            )

        self.function = fn

        if pipeline_alarm_topic is not None:
            topic = pipeline_alarm_topic
            sched_dims = {"ScheduleGroup": "default", "ScheduleName": schedule_name}
            if schedule_enabled:
                cloudwatch.Alarm(
                    self,
                    "HevySchedulerTargetErrors",
                    alarm_name=f"soma-{env_name}-hevy-ingest-scheduler-target-errors",
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
                    alarm_description="Hevy ingest: Scheduler TargetErrorCount (Lambda error response).",
                ).add_alarm_action(cw_actions.SnsAction(topic))
                cloudwatch.Alarm(
                    self,
                    "HevySchedulerInvocationDropped",
                    alarm_name=f"soma-{env_name}-hevy-ingest-scheduler-invocations-dropped",
                    metric=cloudwatch.Metric(
                        namespace="AWS/Scheduler",
                        metric_name="InvocationDroppedCount",
                        dimensions_map=sched_dims,
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.minutes(5),
                    ),
                    threshold=1,
                    evaluation_periods=1,
                    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                    alarm_description="Hevy ingest: Scheduler exhausted retries (InvocationDroppedCount).",
                ).add_alarm_action(cw_actions.SnsAction(topic))
            cloudwatch.Alarm(
                self,
                "HevyLambdaErrors",
                alarm_name=f"soma-{env_name}-hevy-ingest-lambda-errors",
                metric=fn.metric_errors(statistic=cloudwatch.Stats.SUM, period=Duration.minutes(5)),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description="Hevy ingest Lambda reported Errors (uncaught exception, timeout, etc.).",
            ).add_alarm_action(cw_actions.SnsAction(topic))

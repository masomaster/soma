"""Phase 5/6 infrastructure: the daily briefing pipeline (schedule + Lambda).

Implements the Phase 5 "one daily pipeline, single scheduled start" pattern:
**Amazon EventBridge Scheduler** invokes one Lambda well before the user's
briefing time; the Lambda runs the ordered steps in :mod:`pipeline.orchestration`.
Least-priv IAM grants SSM rule-threshold reads, Secrets Manager read for runtime
config, and SES send.

Also wires **SNS + CloudWatch alarms** for Scheduler ``TargetErrorCount`` (failed
target delivery), Lambda errors/throttles, and per-user pipeline failures (log
metric filter on the handler's ``Daily pipeline failed for user`` line). Set CDK
context ``soma:pipelineAlarmEmail`` to deliver alerts via SES (Lambda subscriber —
no SNS email confirmation click).

The ``pipeline`` package and ``psycopg2-binary`` are bundled into a **Lambda
layer** at synth/deploy time via **local** ``pip`` (no Docker) — see
:mod:`soma_cdk.pipeline_layer`. The handler asset (``infrastructure/lambda/briefing/``)
stays handler-only.
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, TimeZone
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct

from soma_cdk.alarms import wire_pipeline_alarm
from soma_cdk.config import DEPLOYED_ENV
from soma_cdk.pipeline_layer import build_pipeline_deps_layer
from soma_cdk.runtime_secrets import RuntimeSecrets

_LAMBDA_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "briefing")
_ALARM_NOTIFY_ASSET = os.path.join(
    os.path.dirname(__file__), "..", "lambda", "alarm_notify"
)


class DailyBriefingPipeline(Construct):
    """EventBridge **Scheduler** daily cron → briefing Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime_secrets: RuntimeSecrets,
        schedule_hour_utc: int = 11,
        deps_layer: lambda_.ILayerVersion | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        if deps_layer is not None:
            layer: lambda_.ILayerVersion = deps_layer
        else:
            layer = build_pipeline_deps_layer(self, construct_id="PipelineDepsLayer")

        # Phase 10 personal guidelines corpus (my-goals.md / injury-history.md /
        # expert-principles.md at guidelines/{user_id}/...). RETAIN so hand-authored
        # guidance is never destroyed on stack delete; the briefing reads it into prompts.
        self.guidelines_bucket = s3.Bucket(
            self,
            "GuidelinesBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )

        self.function = lambda_.Function(
            self,
            "BriefingFunction",
            function_name="soma-daily-briefing",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_LAMBDA_ASSET),
            layers=[layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "ENV": DEPLOYED_ENV,
                "SOMA_GUIDELINES_BUCKET": self.guidelines_bucket.bucket_name,
                **runtime_secrets.env_briefing(),
            },
        )
        # Briefing only reads guidelines; chat-driven writes use a separate path.
        self.guidelines_bucket.grant_read(self.function)
        CfnOutput(
            self,
            "GuidelinesBucketName",
            value=self.guidelines_bucket.bucket_name,
            description="S3 bucket holding per-user guidelines markdown (guidelines/{user_id}/...)",
        )

        region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
        account = os.environ.get("CDK_DEFAULT_ACCOUNT", "*")
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{region}:{account}:parameter/soma/*"],
            )
        )
        runtime_secrets.grant_briefing(self.function)
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"])
        )

        schedule_name = "soma-daily-pipeline"
        # Id ``SchedulerDailyCron`` (not ``DailySchedule``): new CFN logical id so Rule→Schedule is not an in-place type swap.
        self.schedule = scheduler.Schedule(
            self,
            "SchedulerDailyCron",
            schedule_name=schedule_name,
            schedule=scheduler.ScheduleExpression.cron(
                minute="0",
                hour=str(schedule_hour_utc),
                time_zone=TimeZone.ETC_UTC,
            ),
            target=scheduler_targets.LambdaInvoke(self.function),
            description="Daily briefing pipeline (EventBridge Scheduler → Lambda)",
        )

        # --- Operator alerts (Phase 6): SNS + CloudWatch alarms + SES email ---
        stack = Stack.of(self)
        alarm_topic = sns.Topic(
            self,
            "PipelineAlarmTopic",
            topic_name="soma-daily-pipeline-alarms",
            display_name="Soma daily pipeline alarms",
        )
        alarm_email = stack.node.try_get_context("soma:pipelineAlarmEmail")
        if isinstance(alarm_email, str) and alarm_email.strip():
            # SES bridge (not SNS EmailSubscription): confirmed inbox not required.
            notify_fn = lambda_.Function(
                self,
                "AlarmNotifyFunction",
                function_name="soma-pipeline-alarm-notify",
                runtime=lambda_.Runtime.PYTHON_3_14,
                architecture=lambda_.Architecture.X86_64,
                handler="handler.handler",
                code=lambda_.Code.from_asset(_ALARM_NOTIFY_ASSET),
                layers=[layer],
                timeout=Duration.seconds(30),
                memory_size=256,
                log_retention=logs.RetentionDays.ONE_MONTH,
                environment={
                    "ENV": DEPLOYED_ENV,
                    "ALARM_TO_EMAIL": alarm_email.strip(),
                    "SOMA_BRIEFING_SECRET_ARN": runtime_secrets.briefing_arn,
                },
            )
            runtime_secrets.grant_alarm_notify(notify_fn)
            notify_fn.add_to_role_policy(
                iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"])
            )
            alarm_topic.add_subscription(sns_subs.LambdaSubscription(notify_fn))
            CfnOutput(
                self,
                "PipelineAlarmNotifyEmail",
                value=alarm_email.strip(),
                description="Operator inbox for pipeline CloudWatch alarms (via SES)",
            )

        # Default schedule group; must match ``Schedule`` when ``schedule_group`` is omitted.
        _sched_dims = {"ScheduleGroup": "default", "ScheduleName": schedule_name}

        scheduler_target_errors = cloudwatch.Metric(
            namespace="AWS/Scheduler",
            metric_name="TargetErrorCount",
            dimensions_map=_sched_dims,
            statistic=cloudwatch.Stats.SUM,
            period=Duration.minutes(5),
        )
        wire_pipeline_alarm(
            cloudwatch.Alarm(
                self,
                "SchedulerTargetErrors",
                alarm_name="soma-daily-pipeline-scheduler-target-errors",
                metric=scheduler_target_errors,
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description=(
                    "EventBridge Scheduler TargetErrorCount for this schedule (Lambda returned an error). "
                    "Does not cover invoke throttles or retries exhausted; see companion dropped-count alarm."
                ),
            ),
            alarm_topic,
        )

        scheduler_invocation_dropped = cloudwatch.Metric(
            namespace="AWS/Scheduler",
            metric_name="InvocationDroppedCount",
            dimensions_map=_sched_dims,
            statistic=cloudwatch.Stats.SUM,
            period=Duration.minutes(5),
        )
        wire_pipeline_alarm(
            cloudwatch.Alarm(
                self,
                "SchedulerInvocationDropped",
                alarm_name="soma-daily-pipeline-scheduler-invocations-dropped",
                metric=scheduler_invocation_dropped,
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description=(
                    "EventBridge Scheduler stopped retrying this schedule (InvocationDroppedCount). "
                    "Check target permissions, DLQ, and Scheduler retry policy."
                ),
            ),
            alarm_topic,
        )

        wire_pipeline_alarm(
            cloudwatch.Alarm(
                self,
                "BriefingLambdaErrors",
                alarm_name="soma-daily-briefing-lambda-errors",
                metric=self.function.metric_errors(
                    statistic=cloudwatch.Stats.SUM,
                    period=Duration.minutes(5),
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description="Lambda runtime reported at least one failed invocation (uncaught exception, timeout, etc.).",
            ),
            alarm_topic,
        )

        wire_pipeline_alarm(
            cloudwatch.Alarm(
                self,
                "BriefingLambdaThrottles",
                alarm_name="soma-daily-briefing-lambda-throttles",
                metric=self.function.metric_throttles(
                    statistic=cloudwatch.Stats.SUM,
                    period=Duration.minutes(5),
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description="Daily briefing Lambda was throttled (concurrency or account limits).",
            ),
            alarm_topic,
        )

        # Per-user pipeline failures are caught in the handler (Lambda still succeeds):
        # match the handler log line from infrastructure/lambda/briefing/handler.py
        user_pipeline_failures = logs.MetricFilter(
            self,
            "UserPipelineFailureMetric",
            log_group=self.function.log_group,
            filter_pattern=logs.FilterPattern.all_terms(
                "Daily", "pipeline", "failed", "for", "user"
            ),
            metric_namespace="Soma/DailyBriefing",
            metric_name="user_pipeline_failures",
            metric_value="1",
            default_value=0,
        )
        wire_pipeline_alarm(
            cloudwatch.Alarm(
                self,
                "BriefingUserPipelineFailures",
                alarm_name="soma-daily-briefing-user-pipeline-failures",
                metric=user_pipeline_failures.metric(
                    statistic=cloudwatch.Stats.SUM,
                    period=Duration.minutes(5),
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description=(
                    "At least one user run logged 'Daily pipeline failed for user' "
                    "(handler caught DB/LLM/etc. errors for that tenant)."
                ),
            ),
            alarm_topic,
        )

        self.alarm_topic = alarm_topic
        CfnOutput(
            self,
            "PipelineAlarmTopicName",
            value=alarm_topic.topic_name,
            description="SNS topic for pipeline CloudWatch alarms",
        )

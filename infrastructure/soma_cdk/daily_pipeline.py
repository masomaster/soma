"""Phase 5/6 infrastructure: the daily briefing pipeline (schedule + Lambda).

Implements the Phase 5 "one daily pipeline, single scheduled start" pattern:
**Amazon EventBridge Scheduler** invokes one Lambda well before the user's
briefing time; the Lambda runs the ordered steps in :mod:`pipeline.orchestration`.
Least-priv IAM grants SSM rule-threshold reads, Secrets Manager read for runtime
config, and SES send.

Also wires **SNS + CloudWatch alarms** for Scheduler ``TargetErrorCount`` (failed
target delivery), Lambda errors/throttles, and per-user pipeline failures (log
metric filter on the handler's ``Daily pipeline failed for user`` line). Set CDK
context ``soma:pipelineAlarmEmail`` to subscribe an operator inbox (confirm in SNS).

The ``pipeline`` package and ``psycopg2-binary`` are bundled into a **Lambda
layer** at synth/deploy time via **local** ``pip`` (no Docker) — see
:mod:`soma_cdk.pipeline_layer`. The handler asset (``infrastructure/lambda/briefing/``)
stays handler-only.
"""

from __future__ import annotations

import os

from aws_cdk import Duration, Stack, TimeZone
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct

from soma_cdk.pipeline_layer import build_pipeline_deps_layer
from soma_cdk.runtime_secrets import RuntimeSecrets

_LAMBDA_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "briefing")


class DailyBriefingPipeline(Construct):
    """EventBridge **Scheduler** daily cron → briefing Lambda for one environment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        runtime_secrets: RuntimeSecrets,
        schedule_hour_utc: int = 11,
        deps_layer: lambda_.ILayerVersion | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        if deps_layer is not None:
            layer: lambda_.ILayerVersion = deps_layer
        else:
            layer = build_pipeline_deps_layer(self, construct_id="PipelineDepsLayer")

        fn_name = f"soma-{env_name}-daily-briefing"
        self.function = lambda_.Function(
            self,
            "BriefingFunction",
            function_name=fn_name,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_LAMBDA_ASSET),
            layers=[layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "ENV": env_name,
                "SOMA_RULES_PREFIX": f"/soma/{env_name}/",
                **runtime_secrets.env_briefing(),
            },
        )

        region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
        account = os.environ.get("CDK_DEFAULT_ACCOUNT", "*")
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{region}:{account}:parameter/soma/{env_name}/*"],
            )
        )
        runtime_secrets.grant_briefing(self.function)
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"])
        )

        schedule_name = f"soma-{env_name}-daily-pipeline"
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

        # --- Operator alerts (Phase 6): SNS + CloudWatch alarms ---
        stack = Stack.of(self)
        alarm_topic = sns.Topic(
            self,
            "PipelineAlarmTopic",
            topic_name=f"soma-{env_name}-daily-pipeline-alarms",
            display_name=f"Soma {env_name} daily pipeline alarms",
        )
        alarm_email = stack.node.try_get_context("soma:pipelineAlarmEmail")
        if isinstance(alarm_email, str) and alarm_email.strip():
            alarm_topic.add_subscription(sns_subs.EmailSubscription(alarm_email.strip()))

        # Default schedule group; must match ``Schedule`` when ``schedule_group`` is omitted.
        _sched_dims = {"ScheduleGroup": "default", "ScheduleName": schedule_name}

        scheduler_target_errors = cloudwatch.Metric(
            namespace="AWS/Scheduler",
            metric_name="TargetErrorCount",
            dimensions_map=_sched_dims,
            statistic=cloudwatch.Stats.SUM,
            period=Duration.minutes(5),
        )
        cloudwatch.Alarm(
            self,
            "SchedulerTargetErrors",
            alarm_name=f"soma-{env_name}-daily-pipeline-scheduler-target-errors",
            metric=scheduler_target_errors,
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                "EventBridge Scheduler TargetErrorCount for this schedule (Lambda returned an error). "
                "Does not cover invoke throttles or retries exhausted; see companion dropped-count alarm."
            ),
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        scheduler_invocation_dropped = cloudwatch.Metric(
            namespace="AWS/Scheduler",
            metric_name="InvocationDroppedCount",
            dimensions_map=_sched_dims,
            statistic=cloudwatch.Stats.SUM,
            period=Duration.minutes(5),
        )
        cloudwatch.Alarm(
            self,
            "SchedulerInvocationDropped",
            alarm_name=f"soma-{env_name}-daily-pipeline-scheduler-invocations-dropped",
            metric=scheduler_invocation_dropped,
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                "EventBridge Scheduler stopped retrying this schedule (InvocationDroppedCount). "
                "Check target permissions, DLQ, and Scheduler retry policy."
            ),
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        cloudwatch.Alarm(
            self,
            "BriefingLambdaErrors",
            alarm_name=f"soma-{env_name}-daily-briefing-lambda-errors",
            metric=self.function.metric_errors(
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Lambda runtime reported at least one failed invocation (uncaught exception, timeout, etc.).",
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        cloudwatch.Alarm(
            self,
            "BriefingLambdaThrottles",
            alarm_name=f"soma-{env_name}-daily-briefing-lambda-throttles",
            metric=self.function.metric_throttles(
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Daily briefing Lambda was throttled (concurrency or account limits).",
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

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
        cloudwatch.Alarm(
            self,
            "BriefingUserPipelineFailures",
            alarm_name=f"soma-{env_name}-daily-briefing-user-pipeline-failures",
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
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        self.alarm_topic = alarm_topic

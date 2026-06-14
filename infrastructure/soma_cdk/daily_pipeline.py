"""Phase 5/6 infrastructure: the daily briefing pipeline (schedule + Lambda).

Implements the Phase 5 "one daily pipeline, single scheduled start" pattern: a
single EventBridge rule fires one Lambda well before the user's briefing time;
the Lambda runs the ordered steps in :mod:`pipeline.orchestration`. Least-priv
IAM grants SSM rule-threshold reads, Secrets Manager read for runtime config,
and SES send.

Also wires **SNS + CloudWatch alarms** for EventBridge failed invocations, Lambda
errors/throttles, and per-user pipeline failures (log metric filter on the
handler's ``Daily pipeline failed for user`` line). Set CDK context
``soma:pipelineAlarmEmail`` to subscribe an operator inbox (confirm in SNS).

The ``pipeline`` package and ``psycopg2-binary`` are bundled into a **Lambda
layer** at synth/deploy time via **local** ``pip`` (no Docker) — see
:mod:`soma_cdk.pipeline_layer`. The handler asset (``infrastructure/lambda/briefing/``)
stays handler-only.
"""

from __future__ import annotations

import json
import os

from aws_cdk import Aws, CfnCondition, CfnDeletionPolicy, CfnParameter, Duration, Fn, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct

from soma_cdk.pipeline_layer import build_pipeline_deps_layer

_LAMBDA_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "briefing")

_RUNTIME_SECRET_PLACEHOLDER = json.dumps(
    {
        "DB_CONNECT_STRING": "update_me",
        "ANTHROPIC_API_KEY": "update_me",
        "SES_SENDER": "update_me",
        "APPLE_HEALTH_WEBHOOK_SECRET": "update_me",
        "HEVY_API_KEY": "update_me",
        "SOMA_USER_ID": "update_me",
    }
)


class DailyBriefingPipeline(Construct):
    """EventBridge daily schedule → briefing Lambda for one environment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        schedule_hour_utc: int = 11,
        deps_layer: lambda_.ILayerVersion | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        if deps_layer is not None:
            layer: lambda_.ILayerVersion = deps_layer
        else:
            layer = build_pipeline_deps_layer(self, construct_id="PipelineDepsLayer")

        stack = Stack.of(self)
        seed_runtime_secret = CfnParameter(
            stack,
            f"{env_name.capitalize()}SeedLambdaRuntimeSecret",
            type="String",
            default="Yes",
            allowed_values=["Yes", "No"],
            description=(
                "Yes: CloudFormation may set/reset the runtime secret JSON to update_me. "
                "After replacing values in Secrets Manager, deploy with No so updates stop "
                "sending SecretString (your console values are kept)."
            ),
        )
        seed_yes = CfnCondition(
            self,
            f"{env_name.capitalize()}SeedLambdaRuntimeSecretYes",
            expression=Fn.condition_equals(seed_runtime_secret.value_as_string, "Yes"),
        )

        runtime_secret = secretsmanager.CfnSecret(
            self,
            "LambdaRuntimeSecret",
            name=f"soma-{env_name}-lambda-runtime",
            description="DB URI, Anthropic key, SES From, optional Apple webhook HMAC, Hevy key + SOMA_USER_ID (JSON) for Lambdas",
        )
        runtime_secret.add_property_override(
            "SecretString",
            Fn.condition_if(seed_yes.logical_id, _RUNTIME_SECRET_PLACEHOLDER, Aws.NO_VALUE),
        )
        runtime_secret.cfn_options.deletion_policy = CfnDeletionPolicy.RETAIN
        runtime_secret.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN

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
                "SOMA_LAMBDA_SECRET_ARN": runtime_secret.ref,
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
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[runtime_secret.ref],
            )
        )
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"])
        )

        self.rule = events.Rule(
            self,
            "DailySchedule",
            rule_name=f"soma-{env_name}-daily-pipeline",
            schedule=events.Schedule.cron(minute="0", hour=str(schedule_hour_utc)),
        )
        self.rule.add_target(targets.LambdaFunction(self.function))

        # --- Operator alerts (Phase 6): SNS + CloudWatch alarms ---
        alarm_topic = sns.Topic(
            self,
            "PipelineAlarmTopic",
            topic_name=f"soma-{env_name}-daily-pipeline-alarms",
            display_name=f"Soma {env_name} daily pipeline alarms",
        )
        alarm_email = stack.node.try_get_context("soma:pipelineAlarmEmail")
        if isinstance(alarm_email, str) and alarm_email.strip():
            alarm_topic.add_subscription(sns_subs.EmailSubscription(alarm_email.strip()))

        failed_invocations = cloudwatch.Metric(
            namespace="AWS/Events",
            metric_name="FailedInvocations",
            dimensions_map={"RuleName": self.rule.rule_name},
            statistic=cloudwatch.Stats.SUM,
            period=Duration.minutes(5),
        )
        cloudwatch.Alarm(
            self,
            "EventRuleFailedInvocations",
            alarm_name=f"soma-{env_name}-daily-pipeline-rule-failures",
            metric=failed_invocations,
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="EventBridge could not invoke the daily briefing Lambda (permissions, DLQ, or target errors).",
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

        self.runtime_secret_ref = runtime_secret.ref

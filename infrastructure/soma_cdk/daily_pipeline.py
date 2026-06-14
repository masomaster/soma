"""Phase 5/6 infrastructure: the daily briefing pipeline (schedule + Lambda).

Implements the Phase 5 "one daily pipeline, single scheduled start" pattern: a
single EventBridge rule fires one Lambda well before the user's briefing time;
the Lambda runs the ordered steps in :mod:`pipeline.orchestration`. Least-priv
IAM grants SSM rule-threshold reads, Secrets Manager read for runtime config,
and SES send.

The ``pipeline`` package and ``psycopg2-binary`` are bundled into a **Lambda
layer** at synth/deploy time via **local** ``pip`` (no Docker) — see
:mod:`soma_cdk.pipeline_layer`. The handler asset (``infrastructure/lambda/briefing/``)
stays handler-only.
"""

from __future__ import annotations

import json
import os

from aws_cdk import Aws, CfnCondition, CfnDeletionPolicy, CfnParameter, Duration, Fn, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from soma_cdk.pipeline_layer import build_pipeline_deps_layer

_LAMBDA_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "briefing")

_RUNTIME_SECRET_PLACEHOLDER = json.dumps(
    {
        "DB_CONNECT_STRING": "update_me",
        "ANTHROPIC_API_KEY": "update_me",
        "SES_SENDER": "update_me",
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
    ) -> None:
        super().__init__(scope, construct_id)

        deps_layer = build_pipeline_deps_layer(self, construct_id="PipelineDepsLayer")

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
            description="DB URI, Anthropic key, SES From (JSON) for daily briefing Lambda",
        )
        runtime_secret.add_property_override(
            "SecretString",
            Fn.condition_if(seed_yes.logical_id, _RUNTIME_SECRET_PLACEHOLDER, Aws.NO_VALUE),
        )
        runtime_secret.cfn_options.deletion_policy = CfnDeletionPolicy.RETAIN
        runtime_secret.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN

        self.function = lambda_.Function(
            self,
            "BriefingFunction",
            function_name=f"soma-{env_name}-daily-briefing",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_LAMBDA_ASSET),
            layers=[deps_layer],
            timeout=Duration.minutes(5),
            memory_size=512,
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

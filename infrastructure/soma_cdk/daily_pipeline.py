"""Phase 5/6 infrastructure: the daily briefing pipeline (schedule + Lambda).

Implements the Phase 5 "one daily pipeline, single scheduled start" pattern: a
single EventBridge rule fires one Lambda well before the user's briefing time;
the Lambda runs the ordered steps in :mod:`pipeline.orchestration`. Least-priv
IAM grants only SSM rule-threshold reads and SES send.

Packaging note: the Lambda asset (``infrastructure/lambda/briefing/``) holds the
handler + thin client builders. The ``pipeline`` package and ``psycopg2`` are
supplied to the function via a **layer** (or container image) at deploy time —
see ``infrastructure/lambda/briefing/README.md``. Synthesis does not require that
layer, so ``cdk synth`` stays Docker-free in CI.
"""

from __future__ import annotations

import os

from aws_cdk import Duration
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

_LAMBDA_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "briefing")


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

        self.function = lambda_.Function(
            self,
            "BriefingFunction",
            function_name=f"soma-{env_name}-daily-briefing",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_LAMBDA_ASSET),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "ENV": env_name,
                # SSM tree for per-user rule thresholds: /soma/{env}/{user_id}/rules/
                "SOMA_RULES_PREFIX": f"/soma/{env_name}/",
            },
        )

        # Least-privilege: read only this env's rule thresholds, send only SES email.
        region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
        account = os.environ.get("CDK_DEFAULT_ACCOUNT", "*")
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{region}:{account}:parameter/soma/{env_name}/*"],
            )
        )
        self.function.add_to_role_policy(
            iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"])
        )

        # Single daily start, well before the 06:00 local briefing time.
        self.rule = events.Rule(
            self,
            "DailySchedule",
            rule_name=f"soma-{env_name}-daily-pipeline",
            schedule=events.Schedule.cron(minute="0", hour=str(schedule_hour_utc)),
        )
        self.rule.add_target(targets.LambdaFunction(self.function))

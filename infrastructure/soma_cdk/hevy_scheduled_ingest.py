"""EventBridge daily schedule → Lambda: Hevy API → raw S3 → ``strength_events``.

Uses the **same** raw bucket as :class:`soma_cdk.apple_health_ingest.AppleHealthIngestApi`
and the **same** ``soma-{env}-lambda-runtime`` secret as the briefing / Apple Lambdas.
Schedule defaults to **09:00 UTC**, before :class:`soma_cdk.daily_pipeline.DailyBriefingPipeline`
(11:00 UTC) so strength data is fresh for the daily pipeline.
"""

from __future__ import annotations

import os

from aws_cdk import Duration
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

_HEVY_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "hevy_ingest")


class HevyScheduledIngest(Construct):
    """S3 raw bucket (shared) + EventBridge + Lambda for scheduled Hevy pulls."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        deps_layer: lambda_.ILayerVersion,
        runtime_secret_ref: str,
        raw_bucket: s3.IBucket,
        schedule_hour_utc: int = 9,
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
                "SOMA_LAMBDA_SECRET_ARN": runtime_secret_ref,
                "RAW_BUCKET": raw_bucket.bucket_name,
            },
        )
        raw_bucket.grant_put(fn)
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[runtime_secret_ref],
            )
        )

        self.rule = events.Rule(
            self,
            "HevyIngestSchedule",
            rule_name=f"soma-{env_name}-hevy-ingest",
            schedule=events.Schedule.cron(minute="0", hour=str(schedule_hour_utc)),
        )
        self.rule.add_target(targets.LambdaFunction(fn))

        self.function = fn

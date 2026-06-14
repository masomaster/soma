"""Staging environment stack (stable id: **SomaStagingStack**)."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack, Tags
from constructs import Construct

from soma_cdk.apple_health_ingest import AppleHealthIngestApi
from soma_cdk.daily_pipeline import DailyBriefingPipeline
from soma_cdk.hevy_scheduled_ingest import HevyScheduledIngest
from soma_cdk.pipeline_layer import build_pipeline_deps_layer


class SomaStagingStack(Stack):
    """AWS resources for Soma **staging** — add Lambda, S3, EventBridge, etc. here."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "Soma")
        Tags.of(self).add("Environment", "staging")

        pipeline_layer = build_pipeline_deps_layer(self, construct_id="PipelineDeps")
        briefing = DailyBriefingPipeline(
            self, "DailyBriefing", env_name="staging", deps_layer=pipeline_layer
        )
        apple = AppleHealthIngestApi(
            self,
            "AppleHealthIngest",
            env_name="staging",
            deps_layer=pipeline_layer,
            runtime_secret_ref=briefing.runtime_secret_ref,
        )
        HevyScheduledIngest(
            self,
            "HevyScheduledIngest",
            env_name="staging",
            deps_layer=pipeline_layer,
            runtime_secret_ref=briefing.runtime_secret_ref,
            raw_bucket=apple.raw_bucket,
            pipeline_alarm_topic=briefing.alarm_topic,
        )

"""Production environment stack (stable id: **SomaProdStack**)."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack, Tags
from constructs import Construct

from soma_cdk.apple_health_ingest import AppleHealthIngestApi
from soma_cdk.daily_pipeline import DailyBriefingPipeline
from soma_cdk.hevy_scheduled_ingest import HevyScheduledIngest
from soma_cdk.pipeline_layer import build_pipeline_deps_layer
from soma_cdk.runtime_secrets import RuntimeSecrets
from soma_cdk.scheduled_source_ingest import ScheduledSourceIngest
from soma_cdk.weekly_signal_pipeline import WeeklySignalPipeline


class SomaProdStack(Stack):
    """AWS resources for Soma **production** — add Lambda, S3, EventBridge, etc. here."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "Soma")
        Tags.of(self).add("Environment", "prod")

        # Use "prod" (not "production") to match pipeline.settings.Environment and
        # the rules SSM prefix /soma/prod/{user_id}/rules/.
        pipeline_layer = build_pipeline_deps_layer(self, construct_id="PipelineDeps")
        runtime_secrets = RuntimeSecrets(self, "RuntimeSecrets", manage_secrets=False)
        briefing = DailyBriefingPipeline(
            self,
            "DailyBriefing",
            env_name="prod",
            runtime_secrets=runtime_secrets,
            deps_layer=pipeline_layer,
        )
        apple = AppleHealthIngestApi(
            self,
            "AppleHealthIngest",
            env_name="prod",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
        )
        # Lambda only on prod until Phase 11 cutover; no daily cron spend.
        HevyScheduledIngest(
            self,
            "HevyScheduledIngest",
            env_name="prod",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            raw_bucket=apple.raw_bucket,
            schedule_enabled=False,
            pipeline_alarm_topic=briefing.alarm_topic,
        )
        ScheduledSourceIngest(
            self,
            "StravaScheduledIngest",
            env_name="prod",
            source_slug="strava",
            handler_asset_subdir="strava_ingest",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            secret_profile="strava",
            raw_bucket=apple.raw_bucket,
            schedule_hour_utc=8,
            schedule_minute_utc=15,
            schedule_enabled=False,
            pipeline_alarm_topic=briefing.alarm_topic,
        )
        ScheduledSourceIngest(
            self,
            "CalDavScheduledIngest",
            env_name="prod",
            source_slug="caldav",
            handler_asset_subdir="caldav_ingest",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            secret_profile="caldav",
            raw_bucket=apple.raw_bucket,
            schedule_hour_utc=8,
            schedule_minute_utc=0,
            schedule_enabled=False,
            pipeline_alarm_topic=briefing.alarm_topic,
        )
        WeeklySignalPipeline(
            self,
            "WeeklySignal",
            env_name="prod",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            schedule_enabled=False,
            pipeline_alarm_topic=briefing.alarm_topic,
        )

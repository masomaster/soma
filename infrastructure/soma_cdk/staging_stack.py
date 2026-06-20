"""Staging environment stack (stable id: **SomaStagingStack**)."""

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


class SomaStagingStack(Stack):
    """AWS resources for Soma **staging** — add Lambda, S3, EventBridge, etc. here."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "Soma")
        Tags.of(self).add("Environment", "staging")

        pipeline_layer = build_pipeline_deps_layer(self, construct_id="PipelineDeps")
        runtime_secrets = RuntimeSecrets(self, "RuntimeSecrets", manage_secrets=True)
        briefing = DailyBriefingPipeline(
            self,
            "DailyBriefing",
            env_name="staging",
            runtime_secrets=runtime_secrets,
            deps_layer=pipeline_layer,
        )
        apple = AppleHealthIngestApi(
            self,
            "AppleHealthIngest",
            env_name="staging",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
        )
        HevyScheduledIngest(
            self,
            "HevyScheduledIngest",
            env_name="staging",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            raw_bucket=apple.raw_bucket,
            pipeline_alarm_topic=briefing.alarm_topic,
        )
        # Strava live API paused — Lambda for manual invoke; no cron until unpaused.
        ScheduledSourceIngest(
            self,
            "StravaScheduledIngest",
            env_name="staging",
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
            env_name="staging",
            source_slug="caldav",
            handler_asset_subdir="caldav_ingest",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            secret_profile="caldav",
            raw_bucket=apple.raw_bucket,
            schedule_hour_utc=8,
            schedule_minute_utc=0,
            pipeline_alarm_topic=briefing.alarm_topic,
        )
        WeeklySignalPipeline(
            self,
            "WeeklySignal",
            env_name="staging",
            deps_layer=pipeline_layer,
            runtime_secrets=runtime_secrets,
            pipeline_alarm_topic=briefing.alarm_topic,
        )

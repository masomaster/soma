"""Staging environment stack (stable id: **SomaStagingStack**)."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack, Tags
from constructs import Construct

from soma_cdk.daily_pipeline import DailyBriefingPipeline


class SomaStagingStack(Stack):
    """AWS resources for Soma **staging** — add Lambda, S3, EventBridge, etc. here."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "Soma")
        Tags.of(self).add("Environment", "staging")

        DailyBriefingPipeline(self, "DailyBriefing", env_name="staging")

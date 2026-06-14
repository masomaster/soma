"""Production environment stack (stable id: **SomaProdStack**)."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack, Tags
from constructs import Construct

from soma_cdk.daily_pipeline import DailyBriefingPipeline


class SomaProdStack(Stack):
    """AWS resources for Soma **production** — add Lambda, S3, EventBridge, etc. here."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "Soma")
        Tags.of(self).add("Environment", "prod")
   

        # Use "prod" (not "production") to match pipeline.settings.Environment and
        # the rules SSM prefix /soma/prod/{user_id}/rules/.
        DailyBriefingPipeline(self, "DailyBriefing", env_name="prod")

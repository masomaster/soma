"""Phase 9/10: public Streamlit dashboard on AWS App Runner.

Deploys the repo ``dashboard/app.py`` as a container with a default HTTPS URL
(``*.awsapprunner.com``). Supabase Auth + RLS protect user data; secrets come
from Secrets Manager. The briefing Lambda receives ``BRIEFING_EMAIL_DASHBOARD_URL``
so daily emails link to the same host.
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

from soma_cdk.config import DEPLOYED_ENV
from soma_cdk.runtime_secrets import RuntimeSecrets


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class DashboardService(Construct):
    """App Runner service for the Soma Streamlit dashboard."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime_secrets: RuntimeSecrets,
        guidelines_bucket: s3.IBucket,
        briefing_function: lambda_.IFunction,
    ) -> None:
        super().__init__(scope, construct_id)

        stack = Stack.of(self)
        image = ecr_assets.DockerImageAsset(
            self,
            "Image",
            directory=_repo_root(),
            file="dashboard/Dockerfile",
            exclude=[
                "**/.git",
                "**/.venv",
                "**/__pycache__",
                "**/.pytest_cache",
                "**/infrastructure/cdk.out",
                "**/node_modules",
                "**/.bruno",
                "**/tmp",
            ],
        )

        access_role = iam.Role(
            self,
            "EcrAccessRole",
            assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSAppRunnerServicePolicyForECRAccess"
                )
            ],
        )

        instance_role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
        )
        runtime_secrets.grant_dashboard(instance_role)
        guidelines_bucket.grant_read_write(instance_role)

        db_secret = runtime_secrets.db_arn
        dashboard_secret = runtime_secrets.dashboard_arn

        env_vars = [
            apprunner.CfnService.KeyValuePairProperty(name="ENV", value=DEPLOYED_ENV),
            apprunner.CfnService.KeyValuePairProperty(
                name="SOMA_DASHBOARD_FIXTURE", value="0"
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="SOMA_GUIDELINES_BUCKET", value=guidelines_bucket.bucket_name
            ),
        ]
        env_secrets = [
            apprunner.CfnService.KeyValuePairProperty(
                name="SOMA_DATABASE_URL",
                value=db_secret,
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="SUPABASE_URL",
                value=f"{dashboard_secret}:SUPABASE_URL::",
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="SUPABASE_ANON_KEY",
                value=f"{dashboard_secret}:SUPABASE_ANON_KEY::",
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="ANTHROPIC_API_KEY",
                value=f"{dashboard_secret}:ANTHROPIC_API_KEY::",
            ),
        ]

        self.service = apprunner.CfnService(
            self,
            "Service",
            service_name="soma-dashboard",
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                    access_role_arn=access_role.role_arn,
                ),
                auto_deployments_enabled=False,
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=image.image_uri,
                    image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port="8501",
                        runtime_environment_variables=env_vars,
                        runtime_environment_secrets=env_secrets,
                    ),
                ),
            ),
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                cpu="1024",
                memory="2048",
                instance_role_arn=instance_role.role_arn,
            ),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP",
                path="/_stcore/health",
                interval=20,
                timeout=5,
                healthy_threshold=1,
                unhealthy_threshold=5,
            ),
        )

        raw_url = self.service.attr_service_url
        self.service_url = (
            raw_url if str(raw_url).startswith("https://") else f"https://{raw_url}"
        )
        briefing_function.add_environment(
            "BRIEFING_EMAIL_DASHBOARD_URL", self.service_url
        )

        CfnOutput(
            self,
            "DashboardUrl",
            value=self.service_url,
            description="Public HTTPS URL for the Soma Streamlit dashboard",
        )

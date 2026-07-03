"""Phase 9/10: public Streamlit dashboard on ECS Fargate + ALB.

Streamlit requires WebSockets; App Runner does not support them. Fargate behind an
Application Load Balancer does. Secrets come from Secrets Manager; the briefing
Lambda receives ``BRIEFING_EMAIL_DASHBOARD_URL`` for email footers.
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from soma_cdk.config import DEPLOYED_ENV
from soma_cdk.runtime_secrets import RuntimeSecrets


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class DashboardService(Construct):
    """ECS Fargate + public ALB for the Soma Streamlit dashboard."""

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

        cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name="soma-dashboard",
            container_insights=True,
        )

        db_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "DbSecretImport", runtime_secrets.db_arn
        )
        dashboard_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "DashboardSecretImport", runtime_secrets.dashboard_arn
        )

        fargate = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Service",
            service_name="soma-dashboard",
            cluster=cluster,
            cpu=1024,
            memory_limit_mib=2048,
            desired_count=1,
            public_load_balancer=True,
            listener_port=80,
            assign_public_ip=True,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_docker_image_asset(image),
                container_port=8501,
                environment={
                    "ENV": DEPLOYED_ENV,
                    "SOMA_DASHBOARD_FIXTURE": "0",
                    "SOMA_CLOUD_DASHBOARD": "1",
                    "SOMA_GUIDELINES_BUCKET": guidelines_bucket.bucket_name,
                },
                secrets={
                    "SOMA_DATABASE_URL": ecs.Secret.from_secrets_manager(db_secret),
                    "SUPABASE_URL": ecs.Secret.from_secrets_manager(
                        dashboard_secret, field="SUPABASE_URL"
                    ),
                    "SUPABASE_ANON_KEY": ecs.Secret.from_secrets_manager(
                        dashboard_secret, field="SUPABASE_ANON_KEY"
                    ),
                    "ANTHROPIC_API_KEY": ecs.Secret.from_secrets_manager(
                        dashboard_secret, field="ANTHROPIC_API_KEY"
                    ),
                },
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="soma-dashboard",
                    log_retention=logs.RetentionDays.ONE_MONTH,
                ),
            ),
        )

        fargate.target_group.configure_health_check(
            path="/_stcore/health",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )
        fargate.target_group.set_attribute("stickiness.enabled", "true")
        fargate.target_group.set_attribute("stickiness.type", "lb_cookie")

        guidelines_bucket.grant_read_write(fargate.task_definition.task_role)
        runtime_secrets.grant_dashboard(fargate.task_definition.task_role)

        lb_dns = fargate.load_balancer.load_balancer_dns_name
        container = fargate.task_definition.default_container
        if container is not None:
            container.add_environment("STREAMLIT_BROWSER_SERVER_ADDRESS", lb_dns)

        self.service_url = f"http://{lb_dns}"
        briefing_function.add_environment(
            "BRIEFING_EMAIL_DASHBOARD_URL", self.service_url
        )

        CfnOutput(
            self,
            "DashboardUrl",
            value=self.service_url,
            description=(
                "Public HTTP URL for the Soma Streamlit dashboard (ALB). "
                "Add ACM + Route53 for HTTPS on a custom domain."
            ),
        )

"""HTTP API → Lambda for Health Auto Export (Apple Health) webhook ingest.

Writes each POST body to **S3** (raw JSON), normalizes **biometrics** + **cardio_events**
(``pipeline.adapters.apple_health_export`` + ``apple_health_workouts``), upserts to
Postgres using ``soma-db`` and optional ``soma-apple-health-webhook``.

Operator URL (after deploy): ``{apiUrl}/ingest/apple-health`` (``POST``).
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_int
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

from soma_cdk.runtime_secrets import RuntimeSecrets

_WEBHOOK_ASSET = os.path.join(os.path.dirname(__file__), "..", "lambda", "apple_health_webhook")


class AppleHealthIngestApi(Construct):
    """S3 raw bucket + Lambda + HTTP API (POST) for Apple Health / HAE payloads."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        deps_layer: lambda_.ILayerVersion,
        runtime_secrets: RuntimeSecrets,
    ) -> None:
        super().__init__(scope, construct_id)

        removal = RemovalPolicy.DESTROY if env_name == "staging" else RemovalPolicy.RETAIN
        bucket = s3.Bucket(
            self,
            "AppleHealthRawBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            removal_policy=removal,
            auto_delete_objects=env_name == "staging",
        )

        fn = lambda_.Function(
            self,
            "AppleHealthWebhookFn",
            function_name=f"soma-{env_name}-apple-health-webhook",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_WEBHOOK_ASSET),
            layers=[deps_layer],
            timeout=Duration.seconds(30),
            memory_size=256,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "ENV": env_name,
                "RAW_BUCKET": bucket.bucket_name,
                **runtime_secrets.env_apple_health(),
            },
        )
        bucket.grant_put(fn)
        runtime_secrets.grant_apple_health(fn)

        integration = apigwv2_int.HttpLambdaIntegration("AppleHealthLambdaIntegration", fn)
        api = apigwv2.HttpApi(
            self,
            "AppleHealthHttpApi",
            api_name=f"soma-{env_name}-apple-health",
            description="POST Apple Health / Health Auto Export JSON",
        )
        api.add_routes(
            path="/ingest/apple-health",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )

        # HTTP API access logs (every request to the execute-api URL, including 404s).
        # See CloudWatch → Log groups → AppleHealthHttpApiAccessLogs (output below).
        access_log_group = logs.LogGroup(
            self,
            "AppleHealthHttpApiAccessLogs",
            log_group_name=f"/aws/apigateway/soma-{env_name}-apple-health-access",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=removal,
        )
        access_log_group.grant_write(iam.ServicePrincipal("apigateway.amazonaws.com"))

        cfn_stage = api.default_stage.node.default_child
        if not isinstance(cfn_stage, apigwv2.CfnStage):
            raise TypeError(f"Expected CfnStage under default_stage, got {type(cfn_stage)!r}")
        # Format must include $context.requestId (API Gateway requirement).
        access_format = (
            "$context.identity.sourceIp "
            "$context.httpMethod $context.routeKey $context.protocol "
            "$context.status $context.responseLength "
            "$context.integrationStatus $context.integrationLatency "
            "$context.integrationErrorMessage $context.error.message "
            "$context.requestTime $context.requestId"
        )
        cfn_stage.access_log_settings = apigwv2.CfnStage.AccessLogSettingsProperty(
            destination_arn=access_log_group.log_group_arn,
            format=access_format,
        )

        # ``HttpApi.url`` often ends with ``/``; joining ``/ingest/...`` produced ``//`` in
        # CloudFormation outputs. Build the invoke URL explicitly (default ``$default`` stage).
        stack = Stack.of(self)
        ingest_url = Fn.join(
            "",
            [
                "https://",
                api.http_api_id,
                ".execute-api.",
                stack.region,
                ".amazonaws.com/ingest/apple-health",
            ],
        )
        CfnOutput(
            self,
            "AppleHealthIngestUrl",
            value=ingest_url,
            description="POST HAE JSON; X-Soma-User-Id required; X-Soma-Webhook-Secret when webhook key set in apple-health-webhook secret or Lambda env",
        )
        CfnOutput(
            self,
            "AppleHealthHttpApiAccessLogGroup",
            value=access_log_group.log_group_name,
            description="CloudWatch log group for HTTP API access logs (hits, status, integration errors)",
        )

        self.api = api
        self.function = fn
        self.raw_bucket = bucket
        self.ingest_url = ingest_url
        self.access_log_group = access_log_group

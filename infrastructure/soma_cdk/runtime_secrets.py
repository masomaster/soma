"""Per-concern Secrets Manager resources for Soma Lambdas.

Secret names are **not** environment-scoped (``soma-db``, ``soma-briefing``, …) — one
account-wide set shared by staging and prod stacks. Only the stack with
``manage_secrets=True`` creates them; the other imports by name.

CloudFormation parameter ``SeedRuntimeSecrets`` (Yes/No) on the managing stack: when
**Yes**, initial deploy may set placeholder ``SecretString`` values; after filling real
values in the console, redeploy with **No** so updates stop overwriting strings.
"""

from __future__ import annotations

import json

from aws_cdk import Aws, CfnCondition, CfnDeletionPolicy, CfnParameter, Fn, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

_PLACEHOLDER = "update_me"

_BRIEFING_PLACEHOLDER = json.dumps(
    {"ANTHROPIC_API_KEY": _PLACEHOLDER, "SES_SENDER": _PLACEHOLDER}
)

_CALDAV_PLACEHOLDER = json.dumps(
    {
        "CALDAV_URL": "https://caldav.icloud.com",
        "CALDAV_USERNAME": _PLACEHOLDER,
        "CALDAV_PASSWORD": _PLACEHOLDER,
    }
)

# Fixed Secrets Manager names (no env suffix).
NAME_DB = "soma-db"
NAME_BRIEFING = "soma-briefing"
NAME_TENANT = "soma-tenant"
NAME_HEVY = "soma-hevy"
NAME_CALDAV = "soma-caldav"
NAME_APPLE_WEBHOOK = "soma-apple-health-webhook"
NAME_STRAVA = "soma-strava"


def _seed_or_no_value(seed_yes: CfnCondition, placeholder: str) -> object:
    return Fn.condition_if(seed_yes.logical_id, placeholder, Aws.NO_VALUE)


class RuntimeSecrets(Construct):
    """Resolves ``soma-*`` secret ARNs and helpers for Lambda env + IAM."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        manage_secrets: bool = False,
    ) -> None:
        super().__init__(scope, construct_id)

        seed_yes: CfnCondition | None = None
        if manage_secrets:
            stack = Stack.of(self)
            seed_param = CfnParameter(
                stack,
                "SeedRuntimeSecrets",
                type="String",
                default="Yes",
                allowed_values=["Yes", "No"],
                description=(
                    "Yes: CloudFormation may seed placeholder SecretString values on create/update. "
                    "After replacing values in Secrets Manager, deploy with No so your edits are kept."
                ),
            )
            seed_yes = CfnCondition(
                self,
                "SeedRuntimeSecretsYes",
                expression=Fn.condition_equals(seed_param.value_as_string, "Yes"),
            )

        def _bind(
            logical_id: str,
            secret_name: str,
            description: str,
            placeholder: str,
        ) -> str:
            if manage_secrets:
                assert seed_yes is not None
                s = secretsmanager.CfnSecret(
                    self,
                    logical_id,
                    name=secret_name,
                    description=description,
                )
                s.add_property_override(
                    "SecretString",
                    _seed_or_no_value(seed_yes, placeholder),
                )
                s.cfn_options.deletion_policy = CfnDeletionPolicy.RETAIN
                s.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN
                return s.ref
            imported = secretsmanager.Secret.from_secret_name_v2(
                self, logical_id, secret_name
            )
            return imported.secret_arn

        self.db_arn = _bind(
            "DbSecret",
            NAME_DB,
            "Postgres connection URI (Supabase session pooler)",
            _PLACEHOLDER,
        )
        self.briefing_arn = _bind(
            "BriefingSecret",
            NAME_BRIEFING,
            "Anthropic API key + SES From address (JSON)",
            _BRIEFING_PLACEHOLDER,
        )
        self.tenant_arn = _bind(
            "TenantSecret",
            NAME_TENANT,
            "Supabase auth.users UUID for scheduled ingests (plain string)",
            _PLACEHOLDER,
        )
        self.hevy_arn = _bind(
            "HevySecret",
            NAME_HEVY,
            "Hevy Pro API key (plain string)",
            _PLACEHOLDER,
        )
        self.caldav_arn = _bind(
            "CalDavSecret",
            NAME_CALDAV,
            "iCloud CalDAV URL + username + app-specific password (JSON)",
            _CALDAV_PLACEHOLDER,
        )
        self.apple_webhook_arn = _bind(
            "AppleWebhookSecret",
            NAME_APPLE_WEBHOOK,
            "Optional HAE X-Soma-Webhook-Secret (plain string; update_me = disabled)",
            _PLACEHOLDER,
        )
        self.strava_arn = _bind(
            "StravaSecret",
            NAME_STRAVA,
            "Strava OAuth access token when API unpaused (plain string)",
            _PLACEHOLDER,
        )

    def _grant(self, fn: lambda_.IFunction, *secret_arns: str) -> None:
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=list(secret_arns),
            )
        )

    def grant_briefing(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.briefing_arn)

    def grant_apple_health(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.apple_webhook_arn)

    def grant_hevy(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.hevy_arn, self.tenant_arn)

    def grant_caldav(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.caldav_arn, self.tenant_arn)

    def grant_strava(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.strava_arn, self.tenant_arn)

    def grant_weekly_signal(self, fn: lambda_.IFunction) -> None:
        self._grant(fn, self.db_arn, self.briefing_arn, self.tenant_arn)

    def env_briefing(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_BRIEFING_SECRET_ARN": self.briefing_arn,
        }

    def env_apple_health(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_APPLE_WEBHOOK_SECRET_ARN": self.apple_webhook_arn,
        }

    def env_hevy(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_HEVY_SECRET_ARN": self.hevy_arn,
            "SOMA_TENANT_SECRET_ARN": self.tenant_arn,
        }

    def env_caldav(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_CALDAV_SECRET_ARN": self.caldav_arn,
            "SOMA_TENANT_SECRET_ARN": self.tenant_arn,
        }

    def env_strava(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_STRAVA_SECRET_ARN": self.strava_arn,
            "SOMA_TENANT_SECRET_ARN": self.tenant_arn,
        }

    def env_weekly_signal(self) -> dict[str, str]:
        return {
            "SOMA_DB_SECRET_ARN": self.db_arn,
            "SOMA_BRIEFING_SECRET_ARN": self.briefing_arn,
            "SOMA_TENANT_SECRET_ARN": self.tenant_arn,
        }

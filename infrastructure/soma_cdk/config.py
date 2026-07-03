"""Shared CDK constants for the single deployed Soma environment.

Soma has one deployed environment (no staging/prod split). ``DEPLOYED_ENV`` is the
value set as the ``ENV`` Lambda environment variable; it is sourced directly from
:class:`pipeline.settings.Environment` ``CLOUD`` (the same enum the Lambdas resolve
at runtime) so the deploy-time and runtime values cannot drift.
"""

from __future__ import annotations

from pipeline.settings import Environment

DEPLOYED_ENV = Environment.CLOUD.value

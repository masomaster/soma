#!/usr/bin/env python3
"""AWS CDK app entry — registers the single **SomaStack**.

The CloudFormation stack id is intentionally kept as ``SomaStagingStack``: that is
the name of the environment currently deployed, so ``cdk deploy`` performs an
**in-place update** rather than creating a new stack. Reusing the id preserves the
live Apple Health HTTP API URL, the retained raw S3 bucket, and the ``soma-*``
secrets (including the Supabase ``soma-db`` connection). To adopt a clean
``SomaStack`` name later, deploy the new stack, re-point the Apple Health webhook
URL, then delete the old stack (its retained bucket/secrets survive).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo-root `cdk.json` runs `python3 infrastructure/app.py`; ensure `soma_cdk` resolves without PYTHONPATH.
_infra_dir = Path(__file__).resolve().parent
if str(_infra_dir) not in sys.path:
    sys.path.insert(0, str(_infra_dir))

import aws_cdk as cdk

from soma_cdk.soma_stack import SomaStack

# CloudFormation stack name of the live environment; kept for in-place updates.
STACK_ID = "SomaStagingStack"


def main() -> None:
    app = cdk.App()
    account = os.environ.get("CDK_DEFAULT_ACCOUNT")
    region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
    env = cdk.Environment(account=account, region=region) if account else None

    SomaStack(app, STACK_ID, env=env)

    app.synth()


if __name__ == "__main__":
    main()

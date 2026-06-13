#!/usr/bin/env python3
"""AWS CDK app entry — registers **SomaStagingStack** and **SomaProdStack** (stable CloudFormation stack names)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo-root `cdk.json` runs `python3 infrastructure/app.py`; ensure `soma_cdk` resolves without PYTHONPATH.
_infra_dir = Path(__file__).resolve().parent
if str(_infra_dir) not in sys.path:
    sys.path.insert(0, str(_infra_dir))

import aws_cdk as cdk

from soma_cdk.prod_stack import SomaProdStack
from soma_cdk.staging_stack import SomaStagingStack


def main() -> None:
    app = cdk.App()
    account = os.environ.get("CDK_DEFAULT_ACCOUNT")
    region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
    env = cdk.Environment(account=account, region=region) if account else None

    # Construct IDs double as default CloudFormation stack names when deploying by stack id.
    SomaStagingStack(app, "SomaStagingStack", env=env)
    SomaProdStack(app, "SomaProdStack", env=env)

    app.synth()


if __name__ == "__main__":
    main()

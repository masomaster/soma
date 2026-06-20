"""Lambda layer: ``pipeline`` package + ``psycopg2-binary`` (local ``pip``, no Docker).

Bundling runs on the host during ``cdk synth`` / ``cdk deploy`` using the same
Python that runs CDK (see :class:`_PipInstallLayer`). The briefing Lambda is
**x86_64**; on Apple Silicon, ``pip`` is called with ``--platform manylinux2014_x86_64``
so ``psycopg2-binary`` wheels match Lambda.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys

import jsii
from aws_cdk import BundlingOptions, ILocalBundling
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


def _repo_root() -> str:
    """Repository root (parent of ``infrastructure/``)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@jsii.implements(ILocalBundling)
class _PipInstallLayer:
    """Install ``psycopg2-binary``, ``caldav``, then this repo (``--no-deps``) into ``output_dir/python``."""

    def try_bundle(self, output_dir: str, options: BundlingOptions) -> bool:  # noqa: ARG002
        root = _repo_root()
        target = os.path.join(output_dir, "python")
        os.makedirs(target, exist_ok=True)

        base_cmd = [sys.executable, "-m", "pip", "install", "-t", target, "--no-cache-dir"]
        machine = platform.machine().lower()
        # Lambda function uses x86_64; pull manylinux x86 wheels when building on ARM (e.g. M1/M2).
        cross_x86 = []
        if machine in ("arm64", "aarch64"):
            cross_x86 = [
                "--platform",
                "manylinux2014_x86_64",
                "--python-version",
                "3.14",
                "--implementation",
                "cp",
                "--abi",
                "cp314",
                "--only-binary=:all:",
            ]

        layer_packages = ["psycopg2-binary", "caldav"]
        try:
            for pkg in layer_packages:
                subprocess.run([*base_cmd, *cross_x86, pkg], check=True)
            subprocess.run([*base_cmd, *cross_x86, root, "--no-deps"], check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Failed to build Lambda dependency layer with local pip. "
                "Ensure Python 3.14 matches the Lambda runtime and you can reach PyPI. "
                "On Apple Silicon, manylinux x86_64 wheels for psycopg2-binary and caldav must exist for 3.14."
            ) from exc
        return True


def build_pipeline_deps_layer(scope: Construct, *, construct_id: str) -> lambda_.LayerVersion:
    """Layer whose ``python/`` tree contains ``pipeline`` and dependencies."""
    root = _repo_root()
    return lambda_.LayerVersion(
        scope,
        construct_id,
        code=lambda_.Code.from_asset(
            root,
            exclude=[
                "**/.git",
                "**/.venv",
                "**/__pycache__",
                "**/*.pyc",
                "**/.pytest_cache",
                "**/tests",
                "**/infrastructure/cdk.out",
                "**/node_modules",
                "**/.bruno",
                "**/tmp",
            ],
            bundling=BundlingOptions(
                local=_PipInstallLayer(),
                # Image is unused when local bundling succeeds; CDK still requires a placeholder.
                image=lambda_.Runtime.PYTHON_3_14.bundling_image,
            ),
        ),
        compatible_runtimes=[lambda_.Runtime.PYTHON_3_14],
        compatible_architectures=[lambda_.Architecture.X86_64],
        description="Soma pipeline package + psycopg2-binary + caldav",
    )

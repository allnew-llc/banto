# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""AWS SSM Parameter Store driver — uses `aws` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "aws CLI が見つかりません。brew install awscli でインストールしてください。"
)


def _find_aws() -> str:
    path = shutil.which("aws")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class AWSSSMDriver(PlatformDriver):
    """Deploy secrets to AWS SSM Parameter Store.

    `project` is used as the path prefix: /<project>/<env_name>
    """

    def _param_name(self, env_name: str, project: str) -> str:
        return f"/{project}/{env_name}"

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_aws(), "ssm", "get-parameter",
                    "--name", self._param_name(env_name, project),
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_aws(), "ssm", "put-parameter",
                "--name", self._param_name(env_name, project),
                "--value", value,
                "--type", "SecureString",
                "--overwrite",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_aws(), "ssm", "delete-parameter",
                "--name", self._param_name(env_name, project),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

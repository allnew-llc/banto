# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""AWS Secrets Manager driver — uses `aws` CLI.

Unlike SSM Parameter Store, Secrets Manager uses create/update split.
"""
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


class AWSSecretsManagerDriver(PlatformDriver):
    """Deploy secrets to AWS Secrets Manager.

    `project` is used as a name prefix: <project>/<env_name>
    """

    def _secret_id(self, env_name: str, project: str) -> str:
        return f"{project}/{env_name}"

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_aws(), "secretsmanager", "describe-secret",
                    "--secret-id", self._secret_id(env_name, project),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        aws = _find_aws()
        sid = self._secret_id(env_name, project)
        # Try update first
        result = subprocess.run(
            [
                aws, "secretsmanager", "put-secret-value",
                "--secret-id", sid,
                "--secret-string", value,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Secret doesn't exist — create it
        result = subprocess.run(
            [
                aws, "secretsmanager", "create-secret",
                "--name", sid,
                "--secret-string", value,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_aws(), "secretsmanager", "delete-secret",
                "--secret-id", self._secret_id(env_name, project),
                "--force-delete-without-recovery",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

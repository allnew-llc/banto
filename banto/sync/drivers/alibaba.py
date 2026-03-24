# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Alibaba Cloud KMS Secrets Manager driver — uses `aliyun` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "aliyun CLI が見つかりません。brew install aliyun-cli でインストールしてください。"
)


def _find_aliyun() -> str:
    path = shutil.which("aliyun")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class AlibabaKMSDriver(PlatformDriver):
    """Deploy secrets to Alibaba Cloud KMS Secrets Manager.

    `project` is the region ID (e.g., `cn-hangzhou`).
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_aliyun(), "kms", "DescribeSecret",
                    "--SecretName", env_name,
                    "--region", project,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        aliyun = _find_aliyun()
        # Try to add a new version
        result = subprocess.run(
            [
                aliyun, "kms", "PutSecretValue",
                "--SecretName", env_name,
                "--SecretData", value,
                "--VersionId", "vault-latest",
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Create new secret
        result = subprocess.run(
            [
                aliyun, "kms", "CreateSecret",
                "--SecretName", env_name,
                "--SecretData", value,
                "--VersionId", "v1",
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_aliyun(), "kms", "DeleteSecret",
                "--SecretName", env_name,
                "--ForceDeleteWithoutRecovery", "true",
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

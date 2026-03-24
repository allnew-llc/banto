# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tencent Cloud Secrets Manager (SSM) driver — uses `tccli` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "tccli CLI が見つかりません。pip install tccli でインストールしてください。"
)


def _find_tccli() -> str:
    path = shutil.which("tccli")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class TencentSSMDriver(PlatformDriver):
    """Deploy secrets to Tencent Cloud Secrets Manager.

    `project` is the region (e.g., `ap-guangzhou`).
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_tccli(), "ssm", "DescribeSecret",
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
        tccli = _find_tccli()
        # Try update
        result = subprocess.run(
            [
                tccli, "ssm", "PutSecretValue",
                "--SecretName", env_name,
                "--SecretString", value,
                "--VersionId", "vault-latest",
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Create
        result = subprocess.run(
            [
                tccli, "ssm", "CreateSecret",
                "--SecretName", env_name,
                "--SecretString", value,
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_tccli(), "ssm", "DeleteSecret",
                "--SecretName", env_name,
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

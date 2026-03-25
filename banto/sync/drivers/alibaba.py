# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Alibaba Cloud KMS Secrets Manager driver — uses `aliyun` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
The aliyun CLI supports reading parameter values from stdin with the
--SecretData=- convention (file-based input). We use a tempfile approach
since aliyun CLI doesn't natively support stdin for --SecretData.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "aliyun CLI が見つかりません。brew install aliyun-cli でインストールしてください。"
)


def _find_aliyun() -> str:
    path = shutil.which("aliyun")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


def _write_secret_tempfile(value: str) -> str:
    """Write secret to a 0600 tempfile and return the path.

    Caller is responsible for deleting the file after use.
    """
    fd, path = tempfile.mkstemp(prefix="banto-secret-", suffix=".txt")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
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
        # Security: write value to a 0600 tempfile to avoid argv exposure.
        # aliyun CLI doesn't support stdin for --SecretData, so we use
        # a tempfile and read it via file:// URI.
        aliyun = _find_aliyun()
        tmp_path = _write_secret_tempfile(value)
        try:
            # Try to add a new version
            result = subprocess.run(
                [
                    aliyun, "kms", "PutSecretValue",
                    "--SecretName", env_name,
                    "--SecretData", f"file://{tmp_path}",
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
                    "--SecretData", f"file://{tmp_path}",
                    "--VersionId", "v1",
                    "--region", project,
                ],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

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

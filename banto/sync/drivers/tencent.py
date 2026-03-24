# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tencent Cloud Secrets Manager (SSM) driver — uses `tccli` CLI.

Security: secret values are passed via a tempfile with 0o600 permissions
to avoid exposure in `ps aux`. The tccli doesn't support stdin for
--SecretString, so we use a tempfile with file:// URI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "tccli CLI が見つかりません。pip install tccli でインストールしてください。"
)


def _find_tccli() -> str:
    path = shutil.which("tccli")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


def _write_secret_tempfile(value: str) -> str:
    """Write secret to a 0600 tempfile and return the path.

    Caller is responsible for deleting the file after use.
    """
    fd, path = tempfile.mkstemp(prefix="banto-secret-", suffix=".txt")
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
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
        # Security: write value to a 0600 tempfile to avoid argv exposure.
        tccli = _find_tccli()
        tmp_path = _write_secret_tempfile(value)
        try:
            # Try update
            result = subprocess.run(
                [
                    tccli, "ssm", "PutSecretValue",
                    "--SecretName", env_name,
                    "--SecretString", f"file://{tmp_path}",
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
                    "--SecretString", f"file://{tmp_path}",
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
                _find_tccli(), "ssm", "DeleteSecret",
                "--SecretName", env_name,
                "--region", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

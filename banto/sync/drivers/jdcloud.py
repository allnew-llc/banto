# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""JD Cloud KMS driver — uses `jdc` CLI.

Security: secret values are passed via a tempfile with 0o600 permissions
to avoid exposure in `ps aux`. The jdc CLI doesn't support stdin for
--secret-data, so we use a tempfile with file:// URI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "jdc CLI が見つかりません。pip install -U jdcloud_cli でインストールしてください。"
)


def _find_jdc() -> str:
    path = shutil.which("jdc")
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


class JDCloudKMSDriver(PlatformDriver):
    """Deploy secrets to JD Cloud KMS.

    `project` is the region ID (e.g., `cn-north-1`).
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_jdc(), "kms", "describe-secret-list",
                    "--region-id", project,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: write value to a 0600 tempfile to avoid argv exposure.
        jdc = _find_jdc()
        tmp_path = _write_secret_tempfile(value)
        try:
            result = subprocess.run(
                [
                    jdc, "kms", "create-secret",
                    "--secret-name", env_name,
                    "--secret-data", f"file://{tmp_path}",
                    "--region-id", project,
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
                _find_jdc(), "kms", "delete-secret",
                "--secret-name", env_name,
                "--region-id", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

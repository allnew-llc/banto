# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""JD Cloud KMS driver — uses `jdc` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "jdc CLI が見つかりません。pip install -U jdcloud_cli でインストールしてください。"
)


def _find_jdc() -> str:
    path = shutil.which("jdc")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
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
        jdc = _find_jdc()
        result = subprocess.run(
            [
                jdc, "kms", "create-secret",
                "--secret-name", env_name,
                "--secret-data", value,
                "--region-id", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

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

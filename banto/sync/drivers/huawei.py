# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Huawei Cloud CSMS driver — uses `hcloud` (KooCLI)."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "hcloud (KooCLI) が見つかりません。"
    "https://support.huaweicloud.com/intl/en-us/qs-hcli/ を参照してください。"
)


def _find_hcloud() -> str:
    path = shutil.which("hcloud")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class HuaweiCSMSDriver(PlatformDriver):
    """Deploy secrets to Huawei Cloud CSMS (Cloud Secret Management Service).

    `project` is the region (e.g., `ap-southeast-1`).
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_hcloud(), "KMS", "ShowSecret",
                    f"--cli-region={project}",
                    f"--secret_name={env_name}",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        hcloud = _find_hcloud()
        # Try add version
        result = subprocess.run(
            [
                hcloud, "KMS", "CreateSecretVersion",
                f"--cli-region={project}",
                f"--secret_name={env_name}",
                f"--secret_string={value}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Create
        result = subprocess.run(
            [
                hcloud, "KMS", "CreateSecret",
                f"--cli-region={project}",
                f"--secret_name={env_name}",
                f"--secret_string={value}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_hcloud(), "KMS", "DeleteSecret",
                f"--cli-region={project}",
                f"--secret_name={env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

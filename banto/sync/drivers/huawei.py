# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Huawei Cloud CSMS driver — uses `hcloud` (KooCLI).

Security: secret values are passed via a tempfile with 0o600 permissions
to avoid exposure in `ps aux`. hcloud (KooCLI) doesn't support stdin
for --secret_string, so we use a tempfile with file:// URI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

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
        # Security: write value to a 0600 tempfile to avoid argv exposure.
        hcloud = _find_hcloud()
        tmp_path = _write_secret_tempfile(value)
        try:
            # Try add version
            result = subprocess.run(
                [
                    hcloud, "KMS", "CreateSecretVersion",
                    f"--cli-region={project}",
                    f"--secret_name={env_name}",
                    f"--secret_string=file://{tmp_path}",
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
                    f"--secret_string=file://{tmp_path}",
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
                _find_hcloud(), "KMS", "DeleteSecret",
                f"--cli-region={project}",
                f"--secret_name={env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Azure Key Vault driver — uses `az` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "az CLI が見つかりません。brew install azure-cli でインストールしてください。"
)


def _find_az() -> str:
    path = shutil.which("az")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class AzureKeyVaultDriver(PlatformDriver):
    """Deploy secrets to Azure Key Vault.

    `project` is the Key Vault name.
    Note: Azure Key Vault secret names cannot contain underscores,
    so env_name underscores are converted to hyphens.
    """

    @staticmethod
    def _normalize_name(env_name: str) -> str:
        """Convert underscores to hyphens for Azure Key Vault compatibility."""
        return env_name.replace("_", "-")

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_az(), "keyvault", "secret", "show",
                    "--vault-name", project,
                    "--name", self._normalize_name(env_name),
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
                _find_az(), "keyvault", "secret", "set",
                "--vault-name", project,
                "--name", self._normalize_name(env_name),
                "--value", value,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_az(), "keyvault", "secret", "delete",
                "--vault-name", project,
                "--name", self._normalize_name(env_name),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

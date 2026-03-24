# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Google Cloud Secret Manager driver — uses `gcloud` CLI.

Secrets are versioned in GCP. `put` creates a new secret or adds a new version.
"""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "gcloud CLI が見つかりません。brew install google-cloud-sdk でインストールしてください。"
)


def _find_gcloud() -> str:
    path = shutil.which("gcloud")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class GCPSecretManagerDriver(PlatformDriver):
    """Deploy secrets to Google Cloud Secret Manager.

    `project` is the GCP project ID.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_gcloud(), "secrets", "describe", env_name,
                    "--project", project,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        gcloud = _find_gcloud()
        # Try to add a new version to existing secret
        result = subprocess.run(
            [
                gcloud, "secrets", "versions", "add", env_name,
                "--data-file=-", "--project", project,
            ],
            input=value,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Secret doesn't exist yet — create it
        result = subprocess.run(
            [
                gcloud, "secrets", "create", env_name,
                "--data-file=-", "--project", project,
            ],
            input=value,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_gcloud(), "secrets", "delete", env_name,
                "--project", project, "--quiet",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

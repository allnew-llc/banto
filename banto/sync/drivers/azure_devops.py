# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Azure DevOps Pipeline variables driver — uses `az devops` CLI."""
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


class AzureDevOpsDriver(PlatformDriver):
    """Deploy secrets to Azure DevOps pipeline variables.

    `project` is in `org/project` format.
    Uses variable groups or pipeline variables.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            org, proj = project.split("/", 1) if "/" in project else (project, "")
            result = subprocess.run(
                [
                    _find_az(), "pipelines", "variable", "list",
                    "--org", f"https://dev.azure.com/{org}",
                    "--project", proj,
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return f'"{env_name}"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        az = _find_az()
        org, proj = project.split("/", 1) if "/" in project else (project, "")
        # Try update first
        result = subprocess.run(
            [
                az, "pipelines", "variable", "update",
                "--name", env_name, "--value", value,
                "--secret", "true",
                "--org", f"https://dev.azure.com/{org}",
                "--project", proj,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Create
        result = subprocess.run(
            [
                az, "pipelines", "variable", "create",
                "--name", env_name, "--value", value,
                "--secret", "true",
                "--org", f"https://dev.azure.com/{org}",
                "--project", proj,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        org, proj = project.split("/", 1) if "/" in project else (project, "")
        result = subprocess.run(
            [
                _find_az(), "pipelines", "variable", "delete",
                "--name", env_name, "--yes",
                "--org", f"https://dev.azure.com/{org}",
                "--project", proj,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""GitLab CI/CD variables driver — uses `glab` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "glab CLI が見つかりません。brew install glab でインストールしてください。"
)


def _find_glab() -> str:
    path = shutil.which("glab")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class GitLabCIDriver(PlatformDriver):
    """Deploy secrets to GitLab CI/CD variables.

    `project` is the GitLab project path (e.g., `group/project`).
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_glab(), "variable", "list", "-R", project],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return any(
            line.split()[0].strip() == env_name
            for line in result.stdout.splitlines()
            if line.strip()
        )

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Try update first, then create
        result = subprocess.run(
            [
                _find_glab(), "variable", "update", env_name,
                "--value", value, "--masked", "-R", project,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        # Variable doesn't exist — create it
        result = subprocess.run(
            [
                _find_glab(), "variable", "set", env_name,
                "--value", value, "--masked", "-R", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_glab(), "variable", "delete", env_name, "-R", project],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

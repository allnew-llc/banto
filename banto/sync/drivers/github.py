# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""GitHub Actions secrets driver — uses `gh` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = "gh CLI が見つかりません。brew install gh でインストールしてください。"


def _find_gh() -> str:
    path = shutil.which("gh")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class GitHubActionsDriver(PlatformDriver):
    """Deploy secrets to GitHub Actions via gh CLI.

    `project` is the repo in `owner/repo` format.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_gh(), "secret", "list", "-R", project],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return any(
            line.split("\t")[0].strip() == env_name for line in result.stdout.splitlines()
        )

    def put(self, env_name: str, value: str, project: str) -> bool:
        result = subprocess.run(
            [_find_gh(), "secret", "set", env_name, "-R", project, "--body", "-"],
            input=value,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_gh(), "secret", "delete", env_name, "-R", project],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

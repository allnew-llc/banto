# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Railway driver — uses `railway` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "railway CLI が見つかりません。npm i -g @railway/cli でインストールしてください。"
)


def _find_railway() -> str:
    path = shutil.which("railway")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class RailwayDriver(PlatformDriver):
    """Deploy secrets to Railway service variables.

    `project` is the Railway project ID or linked project token.
    Uses RAILWAY_PROJECT_ID env var for project targeting.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_railway(), "variables"],
                capture_output=True,
                text=True,
                env={"RAILWAY_PROJECT_ID": project, "PATH": _get_path()},
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        result = subprocess.run(
            [_find_railway(), "variables", "--set", f"{env_name}={value}"],
            capture_output=True,
            text=True,
            env={"RAILWAY_PROJECT_ID": project, "PATH": _get_path()},
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_railway(), "variables", "delete", env_name],
            capture_output=True,
            text=True,
            env={"RAILWAY_PROJECT_ID": project, "PATH": _get_path()},
        )
        return result.returncode == 0


def _get_path() -> str:
    """Get current PATH for subprocess env."""
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")

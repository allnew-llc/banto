# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Fly.io secrets driver — uses `fly` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = "fly CLI が見つかりません。brew install flyctl でインストールしてください。"


def _find_fly() -> str:
    path = shutil.which("fly") or shutil.which("flyctl")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class FlyIODriver(PlatformDriver):
    """Deploy secrets to Fly.io apps.

    `project` is the Fly app name.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_fly(), "secrets", "list", "-a", project],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return any(
            line.split()[0].strip() == env_name for line in result.stdout.splitlines()
        )

    def put(self, env_name: str, value: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_fly(), "secrets", "set",
                f"{env_name}={value}", "-a", project, "--stage",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_fly(), "secrets", "unset", env_name, "-a", project, "--stage"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

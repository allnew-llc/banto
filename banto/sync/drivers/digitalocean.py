# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""DigitalOcean App Platform driver — uses `doctl` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
The `doctl apps update-env-vars` command reads KEY=VALUE from stdin.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "doctl CLI が見つかりません。brew install doctl でインストールしてください。"
)


def _find_doctl() -> str:
    path = shutil.which("doctl")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class DigitalOceanDriver(PlatformDriver):
    """Deploy secrets to DigitalOcean App Platform.

    `project` is the DigitalOcean app ID.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_doctl(), "apps", "list-env-vars", project,
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        try:
            env_vars = json.loads(result.stdout)
            return any(v.get("key") == env_name for v in env_vars)
        except (json.JSONDecodeError, TypeError):
            return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass KEY=VALUE via stdin to avoid argv exposure in ps aux.
        result = subprocess.run(
            [
                _find_doctl(), "apps", "update-env-vars", project,
            ],
            input=f"{env_name}={value}",
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_doctl(), "apps", "update-env-vars", project,
                f"--unset={env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Deno Deploy env vars driver — uses `deployctl` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
The `deployctl env set` command reads KEY=VALUE from stdin when piped.
"""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "deployctl が見つかりません。deno install -gArf jsr:@deno/deployctl で"
    "インストールしてください。"
)


def _find_deployctl() -> str:
    path = shutil.which("deployctl")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class DenoDeployDriver(PlatformDriver):
    """Deploy secrets to Deno Deploy.

    `project` is the Deno Deploy project name.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_deployctl(), "env", "list", "--project", project],
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
        # Security: pass KEY=VALUE via stdin to avoid argv exposure in ps aux.
        result = subprocess.run(
            [
                _find_deployctl(), "env", "set",
                "--project", project,
            ],
            input=f"{env_name}={value}",
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_deployctl(), "env", "delete", env_name,
                "--project", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

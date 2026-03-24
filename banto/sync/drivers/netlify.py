# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Netlify env vars driver — uses `netlify` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
The `netlify env:set` command reads the value from stdin when piped.
"""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "netlify CLI が見つかりません。npm i -g netlify-cli でインストールしてください。"
)


def _find_netlify() -> str:
    path = shutil.which("netlify")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class NetlifyDriver(PlatformDriver):
    """Deploy secrets to Netlify env vars.

    `project` is the Netlify site ID or name.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_netlify(), "env:list", "--site", project, "--plain"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return any(
            line.startswith(f"{env_name}=") for line in result.stdout.splitlines()
        )

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass value via stdin to avoid argv exposure in ps aux.
        # Netlify CLI reads value from stdin when piped.
        result = subprocess.run(
            [
                _find_netlify(), "env:set", env_name,
                "--site", project, "--context", "production",
            ],
            input=value,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_netlify(), "env:unset", env_name, "--site", project],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

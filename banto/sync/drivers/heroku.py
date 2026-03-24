# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Heroku config vars driver — uses `heroku` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
The `heroku config:set` command reads KEY=VALUE from stdin when piped.
"""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = "heroku CLI が見つかりません。brew install heroku でインストールしてください。"


def _find_heroku() -> str:
    path = shutil.which("heroku")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class HerokuDriver(PlatformDriver):
    """Deploy secrets to Heroku config vars.

    `project` is the Heroku app name.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_heroku(), "config", "-a", project, "--json"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return f'"{env_name}"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass KEY=VALUE via stdin to avoid argv exposure in ps aux.
        # Heroku CLI reads config vars from stdin when piped.
        result = subprocess.run(
            [_find_heroku(), "config:set", "-a", project],
            input=f"{env_name}={value}",
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_heroku(), "config:unset", env_name, "-a", project],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

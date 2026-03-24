# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Cloudflare Pages driver — uses `wrangler` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "wrangler CLI が見つかりません。npm i -g wrangler でインストールしてください。"
)


def _find_wrangler() -> str:
    """Resolve the wrangler binary path, raising FileNotFoundError with guidance."""
    path = shutil.which("wrangler")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class CloudflarePagesDriver(PlatformDriver):
    """Deploy secrets to Cloudflare Pages via wrangler CLI."""

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_wrangler(), "pages", "secret", "list", "--project-name", project],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        return env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_wrangler(), "pages", "secret", "put", env_name,
                "--project-name", project,
            ],
            input=value,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_wrangler(), "pages", "secret", "delete", env_name,
                "--project-name", project, "--force",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Supabase Edge Functions secrets driver — uses `supabase` CLI."""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "supabase CLI が見つかりません。brew install supabase/tap/supabase で"
    "インストールしてください。"
)


def _find_supabase() -> str:
    path = shutil.which("supabase")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class SupabaseDriver(PlatformDriver):
    """Deploy secrets to Supabase Edge Functions.

    `project` is the Supabase project ref.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_supabase(), "secrets", "list",
                    "--project-ref", project,
                ],
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
        result = subprocess.run(
            [
                _find_supabase(), "secrets", "set",
                f"{env_name}={value}",
                "--project-ref", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_supabase(), "secrets", "unset", env_name,
                "--project-ref", project,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

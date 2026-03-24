# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Vercel driver — uses `vercel` CLI.

Vercel CLI requires a linked project directory for env commands.
This driver creates a temporary directory, runs `vercel link --project <name>`,
then uses `--cwd` to target that linked directory for env operations.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = "vercel CLI が見つかりません。npm i -g vercel でインストールしてください。"


def _find_vercel() -> str:
    """Resolve the vercel binary path, raising FileNotFoundError with guidance."""
    path = shutil.which("vercel")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class VercelDriver(PlatformDriver):
    """Deploy secrets to Vercel via vercel CLI with temporary project linking."""

    def _with_linked_dir(self, project: str, callback):
        """Create a temp dir, link it to the Vercel project, run callback, clean up."""
        vercel = _find_vercel()
        tmpdir = tempfile.mkdtemp(prefix="banto-sync-vercel-")
        try:
            link_result = subprocess.run(
                [vercel, "link", "--yes", "--project", project],
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            if link_result.returncode != 0:
                return callback(vercel, tmpdir, linked=False)
            return callback(vercel, tmpdir, linked=True)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def exists(self, env_name: str, project: str) -> bool:
        try:
            _find_vercel()
        except FileNotFoundError:
            return False

        def _check(vercel_bin, cwd, linked):
            if not linked:
                return False
            result = subprocess.run(
                [vercel_bin, "env", "ls", "production", "--cwd", cwd],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False
            return env_name in result.stdout

        return self._with_linked_dir(project, _check)

    def put(self, env_name: str, value: str, project: str) -> bool:
        def _do_put(vercel_bin, cwd, linked):
            if not linked:
                return False
            result = subprocess.run(
                [vercel_bin, "env", "add", env_name, "production",
                 "--force", "--cwd", cwd],
                input=value,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        return self._with_linked_dir(project, _do_put)

    def delete(self, env_name: str, project: str) -> bool:
        def _do_delete(vercel_bin, cwd, linked):
            if not linked:
                return False
            result = subprocess.run(
                [vercel_bin, "env", "rm", env_name, "production",
                 "--yes", "--cwd", cwd],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        return self._with_linked_dir(project, _do_delete)

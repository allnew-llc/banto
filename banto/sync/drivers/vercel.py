# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Vercel driver — uses `vercel` CLI.

Vercel CLI requires a linked project directory for env commands.
This driver creates a temporary directory, runs `vercel link --project <name>`,
then uses `--cwd` to target that linked directory for env operations.

Project strings may carry a team scope as ``<team>/<project>``; the scope part
is passed to ``vercel link --scope`` so resolution does not depend on the
CLI's default team. After linking, ``.vercel/project.json`` is checked to
confirm the link points at the requested project — writes are refused
otherwise (fail closed).

``env add`` deliberately omits ``--sensitive``: non-interactive adds with that
flag report success (exit 0) while silently failing to persist the value —
old value retained on ``--force`` upserts of existing rows, empty value on
fresh adds (vercel/vercel#16160). Values are encrypted at rest either way;
enforce the "sensitive" marking via the team-level sensitive environment
variable policy instead.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import PlatformDriver

_CLI_NOT_FOUND = "vercel CLI が見つかりません。npm i -g vercel でインストールしてください。"


def _find_vercel() -> str:
    """Resolve the vercel binary path, raising FileNotFoundError with guidance."""
    path = shutil.which("vercel")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


def _split_scope(project: str) -> tuple[str | None, str]:
    """Split "<team>/<project>" into (scope, project); scope is None if absent."""
    if "/" in project:
        scope, name = project.split("/", 1)
        if scope and name:
            return scope, name
    return None, project


def _linked_project_matches(tmpdir: str, expected: str) -> bool:
    """Check that .vercel/project.json points at the expected project.

    ``vercel link --yes`` can resolve to (or auto-create) a different project
    than intended, so a zero exit code alone must not authorize writes.
    Older CLI versions omit ``projectName``; for those, the presence of a
    ``projectId`` is accepted.
    """
    try:
        raw = json.loads(
            (Path(tmpdir) / ".vercel" / "project.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    name = raw.get("projectName")
    if name is None:
        return bool(raw.get("projectId"))
    return name == expected


class VercelDriver(PlatformDriver):
    """Deploy secrets to Vercel via vercel CLI with temporary project linking."""

    def _with_linked_dir(self, project: str, callback):
        """Create a temp dir, link it to the Vercel project, run callback, clean up."""
        vercel = _find_vercel()
        scope, name = _split_scope(project)
        tmpdir = tempfile.mkdtemp(prefix="banto-sync-vercel-")
        try:
            link_cmd = [vercel, "link", "--yes", "--project", name]
            if scope:
                link_cmd += ["--scope", scope]
            link_result = subprocess.run(
                link_cmd,
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            linked = (
                link_result.returncode == 0
                and _linked_project_matches(tmpdir, name)
            )
            return callback(vercel, tmpdir, linked=linked)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _env_row_exists(self, vercel_bin: str, cwd: str,
                        env_name: str, environment: str) -> bool:
        """Check `vercel env ls <environment>` for a row named env_name."""
        result = subprocess.run(
            [vercel_bin, "env", "ls", environment, "--cwd", cwd],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields and fields[0] == env_name:
                return True
        return False

    def exists(self, env_name: str, project: str) -> bool:
        try:
            _find_vercel()
        except FileNotFoundError:
            return False

        def _check(vercel_bin, cwd, linked):
            if not linked:
                return False
            return self._env_row_exists(vercel_bin, cwd, env_name, "production")

        return self._with_linked_dir(project, _check)

    def put(self, env_name: str, value: str, project: str,
            environments: list[str] | None = None) -> bool:
        envs = environments or ["production"]

        def _do_put(vercel_bin, cwd, linked):
            if not linked:
                return False
            for env in envs:
                result = subprocess.run(
                    [vercel_bin, "env", "add", env_name, env,
                     "--force", "--cwd", cwd],
                    input=value,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    return False
                if not self._env_row_exists(vercel_bin, cwd, env_name, env):
                    return False
            return True

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

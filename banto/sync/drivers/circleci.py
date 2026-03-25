# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""CircleCI project env vars driver — uses `circleci` CLI.

Security: JSON payloads containing secret values are passed via stdin
(-d @-) and auth tokens via curl config (-K -) to avoid exposure in
`ps aux`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "circleci CLI が見つかりません。brew install circleci でインストールしてください。"
)


def _find_circleci() -> str:
    path = shutil.which("circleci")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class CircleCIDriver(PlatformDriver):
    """Deploy secrets to CircleCI project env vars.

    `project` is in `vcs/org/repo` format (e.g., `github/myorg/myrepo`).
    The circleci CLI wraps the v2 API for env var management.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            _find_circleci()
        except FileNotFoundError:
            return False
        token = os.environ.get("CIRCLECI_TOKEN", "")
        if not token:
            return False
        # Security: pass auth token via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://circleci.com/api/v2/project/{project}/envvar"],
            input=f'-H "Circle-Token: {token}"\n',
            capture_output=True,
            text=True,
        )
        return env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # CircleCI API: POST /project/:project/envvar
        # Security: pass auth token via curl config (-K -) and JSON payload
        # via tempfile (-d @file) to avoid exposing secrets in argv.
        token = os.environ.get("CIRCLECI_TOKEN", "")
        if not token:
            raise FileNotFoundError(
                "CIRCLECI_TOKEN 環境変数を設定してください。"
            )
        payload = json.dumps({"name": env_name, "value": value})
        fd, tmp_path = tempfile.mkstemp(prefix="banto-circleci-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            config = (
                f'-H "Circle-Token: {token}"\n'
                f'-H "Content-Type: application/json"\n'
            )
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://circleci.com/api/v2/project/{project}/envvar"],
                input=config,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and '"name"' in result.stdout
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        token = os.environ.get("CIRCLECI_TOKEN", "")
        if not token:
            return False
        # Security: pass auth token via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-X", "DELETE", "-K", "-",
             f"https://circleci.com/api/v2/project/{project}/envvar/{env_name}"],
            input=f'-H "Circle-Token: {token}"\n',
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

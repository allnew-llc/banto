# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""CircleCI project env vars driver — uses `circleci` CLI."""
from __future__ import annotations

import shutil
import subprocess

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
            result = subprocess.run(
                [_find_circleci(), "context", "list", project],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        # circleci CLI doesn't have a direct env var list command,
        # so we use the API via curl
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", "Circle-Token: $(cat ~/.circleci/cli.yml 2>/dev/null | grep token | cut -d' ' -f2)",
                f"https://circleci.com/api/v2/project/{project}/envvar",
            ],
            capture_output=True,
            text=True,
            shell=False,
        )
        return env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # CircleCI API: POST /project/:project/envvar
        import json
        import os

        token = os.environ.get("CIRCLECI_TOKEN", "")
        if not token:
            raise FileNotFoundError(
                "CIRCLECI_TOKEN 環境変数を設定してください。"
            )
        payload = json.dumps({"name": env_name, "value": value})
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", f"Circle-Token: {token}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"https://circleci.com/api/v2/project/{project}/envvar",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"name"' in result.stdout

    def delete(self, env_name: str, project: str) -> bool:
        import os

        token = os.environ.get("CIRCLECI_TOKEN", "")
        if not token:
            return False
        result = subprocess.run(
            [
                "curl", "-s", "-X", "DELETE",
                "-H", f"Circle-Token: {token}",
                f"https://circleci.com/api/v2/project/{project}/envvar/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

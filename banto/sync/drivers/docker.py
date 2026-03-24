# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Docker Swarm Secrets driver — uses `docker` CLI.

Note: Docker secrets are for Swarm mode only. For Docker Compose,
use the `local` driver with a .env file.
"""
from __future__ import annotations

import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "docker CLI が見つかりません。Docker Desktop をインストールしてください。"
)


def _find_docker() -> str:
    path = shutil.which("docker")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class DockerSwarmDriver(PlatformDriver):
    """Deploy secrets to Docker Swarm.

    `project` is ignored (Docker secrets are cluster-scoped).
    Secret name = env_name.
    """

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [_find_docker(), "secret", "inspect", env_name],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        docker = _find_docker()
        # Docker secrets are immutable — remove then recreate
        subprocess.run(
            [docker, "secret", "rm", env_name],
            capture_output=True,
        )
        result = subprocess.run(
            [docker, "secret", "create", env_name, "-"],
            input=value,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [_find_docker(), "secret", "rm", env_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

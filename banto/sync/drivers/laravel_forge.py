# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Laravel Forge env vars driver — uses REST API via curl."""
from __future__ import annotations

import os
import subprocess

from .base import PlatformDriver


class LaravelForgeDriver(PlatformDriver):
    """Deploy secrets to Laravel Forge server environment.

    `project` is in `server_id/site_id` format.
    Requires FORGE_API_TOKEN environment variable.
    """

    def _token(self) -> str:
        token = os.environ.get("FORGE_API_TOKEN", "")
        if not token:
            raise FileNotFoundError(
                "FORGE_API_TOKEN 環境変数を設定してください。"
            )
        return token

    def _parse_project(self, project: str) -> tuple[str, str]:
        if "/" in project:
            server_id, site_id = project.split("/", 1)
            return server_id, site_id
        return project, ""

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        server_id, site_id = self._parse_project(project)
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/json",
                f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and f"{env_name}=" in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        token = self._token()
        server_id, site_id = self._parse_project(project)
        # GET current env, append/replace, PUT back
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/json",
                f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env",
            ],
            capture_output=True,
            text=True,
        )
        current = result.stdout if result.returncode == 0 else ""
        lines = current.splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{env_name}=")]
        new_lines.append(f"{env_name}={value}")
        new_content = "\n".join(new_lines)

        import json

        result = subprocess.run(
            [
                "curl", "-s", "-X", "PUT",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"content": new_content}),
                f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        server_id, site_id = self._parse_project(project)
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {token}",
                f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        lines = result.stdout.splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{env_name}=")]
        if len(new_lines) == len(lines):
            return False

        import json

        result = subprocess.run(
            [
                "curl", "-s", "-X", "PUT",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"content": "\n".join(new_lines)}),
                f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

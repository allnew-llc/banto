# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Laravel Forge env vars driver — uses REST API via curl.

Security: JSON payloads containing secret values and auth tokens are
passed via stdin/tempfile to avoid exposure in `ps aux`.
Auth headers use curl -K - (config from stdin), and JSON bodies use
-d @file (read from tempfile with 0600 permissions).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

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

    def _curl_config(self, token: str, content_type: bool = False) -> str:
        """Build curl config string for auth headers."""
        config = (
            f'-H "Authorization: Bearer {token}"\n'
            f'-H "Accept: application/json"\n'
        )
        if content_type:
            config += '-H "Content-Type: application/json"\n'
        return config

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        server_id, site_id = self._parse_project(project)
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env"],
            input=self._curl_config(token),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and f"{env_name}=" in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass auth via curl config (-K -) and JSON payload via
        # tempfile (-d @file) to avoid exposing secrets in argv.
        token = self._token()
        server_id, site_id = self._parse_project(project)
        # GET current env
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env"],
            input=self._curl_config(token),
            capture_output=True,
            text=True,
        )
        current = result.stdout if result.returncode == 0 else ""
        lines = current.splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{env_name}=")]
        new_lines.append(f"{env_name}={value}")
        new_content = "\n".join(new_lines)

        payload = json.dumps({"content": new_content})
        fd, tmp_path = tempfile.mkstemp(prefix="banto-forge-", suffix=".json")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            result = subprocess.run(
                ["curl", "-s", "-X", "PUT", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env"],
                input=self._curl_config(token, content_type=True),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        server_id, site_id = self._parse_project(project)
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env"],
            input=self._curl_config(token),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        lines = result.stdout.splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{env_name}=")]
        if len(new_lines) == len(lines):
            return False

        payload = json.dumps({"content": "\n".join(new_lines)})
        fd, tmp_path = tempfile.mkstemp(prefix="banto-forge-", suffix=".json")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            result = subprocess.run(
                ["curl", "-s", "-X", "PUT", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://forge.laravel.com/api/v1/servers/{server_id}/sites/{site_id}/env"],
                input=self._curl_config(token, content_type=True),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Render driver — uses `render` CLI.

Render added official CLI support in 2025. Falls back to REST API via curl
if the CLI is not installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "render CLI が見つかりません。npm i -g @render/cli でインストールするか、"
    "RENDER_API_KEY 環境変数を設定してください。"
)


def _find_render() -> str | None:
    return shutil.which("render")


class RenderDriver(PlatformDriver):
    """Deploy secrets to Render services.

    `project` is the Render service ID (srv-xxx).
    Uses render CLI if available, otherwise falls back to REST API.
    """

    def _api_headers(self) -> dict[str, str]:
        api_key = os.environ.get("RENDER_API_KEY", "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def exists(self, env_name: str, project: str) -> bool:
        render = _find_render()
        if render:
            try:
                result = subprocess.run(
                    [render, "services", "env-vars", "list", "--service", project,
                     "--output", "json"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    env_vars = json.loads(result.stdout)
                    return any(v.get("key") == env_name for v in env_vars)
            except (json.JSONDecodeError, TypeError):
                pass
            return False
        # Fallback: REST API
        result = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-H", f"Authorization: Bearer {os.environ.get('RENDER_API_KEY', '')}",
                f"https://api.render.com/v1/services/{project}/env-vars",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "200":
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {os.environ.get('RENDER_API_KEY', '')}",
                f"https://api.render.com/v1/services/{project}/env-vars",
            ],
            capture_output=True, text=True,
        )
        try:
            env_vars = json.loads(result.stdout)
            return any(v.get("envVar", {}).get("key") == env_name for v in env_vars)
        except (json.JSONDecodeError, TypeError):
            return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        render = _find_render()
        if render:
            result = subprocess.run(
                [render, "services", "env-vars", "set", "--service", project,
                 f"{env_name}={value}"],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        # Fallback: REST API (PUT replaces all — need GET-modify-PUT)
        api_key = os.environ.get("RENDER_API_KEY", "")
        if not api_key:
            raise FileNotFoundError(_CLI_NOT_FOUND)
        # GET current env vars
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {api_key}",
                f"https://api.render.com/v1/services/{project}/env-vars",
            ],
            capture_output=True, text=True,
        )
        try:
            current = json.loads(result.stdout)
            env_vars = [
                {"key": v.get("envVar", {}).get("key"), "value": v.get("envVar", {}).get("value")}
                for v in current
                if v.get("envVar", {}).get("key") != env_name
            ]
        except (json.JSONDecodeError, TypeError):
            env_vars = []
        env_vars.append({"key": env_name, "value": value})
        # PUT all env vars
        result = subprocess.run(
            [
                "curl", "-s", "-X", "PUT",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(env_vars),
                f"https://api.render.com/v1/services/{project}/env-vars",
            ],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        render = _find_render()
        if render:
            result = subprocess.run(
                [render, "services", "env-vars", "unset", "--service", project,
                 env_name],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        # Fallback: REST API DELETE
        api_key = os.environ.get("RENDER_API_KEY", "")
        if not api_key:
            return False
        result = subprocess.run(
            [
                "curl", "-s", "-X", "DELETE",
                "-H", f"Authorization: Bearer {api_key}",
                f"https://api.render.com/v1/services/{project}/env-vars/{env_name}",
            ],
            capture_output=True, text=True,
        )
        return result.returncode == 0

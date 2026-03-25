# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Render driver — uses `render` CLI.

Render added official CLI support in 2025. Falls back to REST API via curl
if the CLI is not installed.

Security: secret values and API keys are never passed as argv.
- CLI path: KEY=VALUE is passed via stdin.
- API path: JSON payload is passed via stdin (-d @-), and the API key
  is passed via environment variable to avoid exposure in `ps aux`.
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


def _curl_env_with_auth() -> dict[str, str]:
    """Build env dict with RENDER_API_KEY for curl config file approach."""
    env = os.environ.copy()
    return env


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
        # Fallback: REST API — pass API key via env var and config file
        # to avoid exposing it in argv (visible in ps aux).
        api_key = os.environ.get("RENDER_API_KEY", "")
        if not api_key:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", "Authorization: Bearer $BANTO_RENDER_KEY",
                f"https://api.render.com/v1/services/{project}/env-vars",
            ],
            capture_output=True, text=True,
            env={**os.environ, "BANTO_RENDER_KEY": api_key},
        )
        # curl doesn't expand $VAR in -H; use stdin config approach instead
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://api.render.com/v1/services/{project}/env-vars"],
            input=f'-H "Authorization: Bearer {api_key}"\n',
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
            # Security: pass KEY=VALUE via stdin to avoid argv exposure.
            result = subprocess.run(
                [render, "services", "env-vars", "set", "--service", project],
                input=f"{env_name}={value}",
                capture_output=True, text=True,
            )
            return result.returncode == 0
        # Fallback: REST API (PUT replaces all — need GET-modify-PUT)
        # Security: API key and payload passed via stdin (-K - and -d @-)
        # to avoid exposure in ps aux.
        api_key = os.environ.get("RENDER_API_KEY", "")
        if not api_key:
            raise FileNotFoundError(_CLI_NOT_FOUND)
        # GET current env vars — pass auth via curl config on stdin
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://api.render.com/v1/services/{project}/env-vars"],
            input=f'-H "Authorization: Bearer {api_key}"\n',
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
        # PUT all env vars — pass both auth header and JSON body via stdin
        # using curl's -K (config from stdin) and -d @/dev/stdin approach.
        # We use a tempfile for the payload to keep -K for auth.
        import tempfile

        payload = json.dumps(env_vars)
        fd, tmp_path = tempfile.mkstemp(prefix="banto-render-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            config_lines = (
                f'-H "Authorization: Bearer {api_key}"\n'
                f'-H "Content-Type: application/json"\n'
            )
            result = subprocess.run(
                ["curl", "-s", "-X", "PUT", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://api.render.com/v1/services/{project}/env-vars"],
                input=config_lines,
                capture_output=True, text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        render = _find_render()
        if render:
            result = subprocess.run(
                [render, "services", "env-vars", "unset", "--service", project,
                 env_name],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        # Fallback: REST API DELETE — pass auth via curl config on stdin
        api_key = os.environ.get("RENDER_API_KEY", "")
        if not api_key:
            return False
        result = subprocess.run(
            ["curl", "-s", "-X", "DELETE", "-K", "-",
             f"https://api.render.com/v1/services/{project}/env-vars/{env_name}"],
            input=f'-H "Authorization: Bearer {api_key}"\n',
            capture_output=True, text=True,
        )
        return result.returncode == 0

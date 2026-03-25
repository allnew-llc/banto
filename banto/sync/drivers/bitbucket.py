# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Bitbucket Pipelines variables driver — uses Bitbucket REST API.

Bitbucket doesn't have a dedicated secrets CLI, so we use the REST API
via curl with BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD.

Security: JSON payloads containing secret values are passed via stdin
(-d @-) and auth credentials via curl config (-K -) to avoid exposure
in `ps aux`.
"""
from __future__ import annotations

import json
import os
import subprocess

from .base import PlatformDriver


def _auth() -> tuple[str, str]:
    """Get Bitbucket credentials from env vars."""
    user = os.environ.get("BITBUCKET_USERNAME", "")
    password = os.environ.get("BITBUCKET_APP_PASSWORD", "")
    if not user or not password:
        raise FileNotFoundError(
            "BITBUCKET_USERNAME と BITBUCKET_APP_PASSWORD 環境変数を設定してください。"
        )
    return user, password


def _curl_config_auth(user: str, password: str) -> str:
    """Build a curl config string for authentication."""
    return f'-u "{user}:{password}"\n'


class BitbucketPipelinesDriver(PlatformDriver):
    """Deploy secrets to Bitbucket Pipelines repository variables.

    `project` is the repository in `workspace/repo` format.
    """

    def _api_url(self, project: str, env_name: str = "") -> str:
        base = f"https://api.bitbucket.org/2.0/repositories/{project}/pipelines_config/variables"
        if env_name:
            # Need to find the UUID first, but for simplicity we list and filter
            return base
        return base

    def exists(self, env_name: str, project: str) -> bool:
        try:
            user, password = _auth()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin to avoid argv exposure.
        result = subprocess.run(
            ["curl", "-s", "-K", "-", self._api_url(project)],
            input=_curl_config_auth(user, password),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout)
            values = data.get("values", [])
            return any(v.get("key") == env_name for v in values)
        except (json.JSONDecodeError, TypeError):
            return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        user, password = _auth()
        payload = json.dumps({
            "key": env_name,
            "value": value,
            "secured": True,
        })
        # Try to delete existing first (Bitbucket doesn't support update)
        self.delete(env_name, project)
        # Security: pass auth via curl config (-K -) and JSON payload via
        # a tempfile (-d @file) to avoid exposing secrets in argv.
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix="banto-bb-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            config = (
                f'-u "{user}:{password}"\n'
                f'-H "Content-Type: application/json"\n'
            )
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 self._api_url(project)],
                input=config,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and '"key"' in result.stdout
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        try:
            user, password = _auth()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-", self._api_url(project)],
            input=_curl_config_auth(user, password),
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
            for v in data.get("values", []):
                if v.get("key") == env_name:
                    uuid = v.get("uuid", "").strip("{}")
                    del_result = subprocess.run(
                        ["curl", "-s", "-X", "DELETE", "-K", "-",
                         f"{self._api_url(project)}/{{{uuid}}}"],
                        input=_curl_config_auth(user, password),
                        capture_output=True,
                        text=True,
                    )
                    return del_result.returncode == 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

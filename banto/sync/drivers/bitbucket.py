# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Bitbucket Pipelines variables driver — uses Bitbucket REST API.

Bitbucket doesn't have a dedicated secrets CLI, so we use the REST API
via curl with BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD.
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
        result = subprocess.run(
            [
                "curl", "-s", "-u", f"{user}:{password}",
                self._api_url(project),
            ],
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
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-u", f"{user}:{password}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                self._api_url(project),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"key"' in result.stdout

    def delete(self, env_name: str, project: str) -> bool:
        try:
            user, password = _auth()
        except FileNotFoundError:
            return False
        # List to find UUID
        result = subprocess.run(
            [
                "curl", "-s", "-u", f"{user}:{password}",
                self._api_url(project),
            ],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
            for v in data.get("values", []):
                if v.get("key") == env_name:
                    uuid = v.get("uuid", "").strip("{}")
                    del_result = subprocess.run(
                        [
                            "curl", "-s", "-X", "DELETE",
                            "-u", f"{user}:{password}",
                            f"{self._api_url(project)}/{{{uuid}}}",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    return del_result.returncode == 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

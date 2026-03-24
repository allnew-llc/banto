# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Naver Cloud Platform Secret Manager driver — uses REST API via curl."""
from __future__ import annotations

import json
import os
import subprocess

from .base import PlatformDriver


class NaverCloudDriver(PlatformDriver):
    """Deploy secrets to Naver Cloud Platform Secret Manager.

    `project` is not used (secrets are account-scoped).
    Requires NCLOUD_ACCESS_KEY_ID and NCLOUD_SECRET_KEY environment variables.
    """

    def _headers(self) -> list[str]:
        ak = os.environ.get("NCLOUD_ACCESS_KEY_ID", "")
        if not ak:
            raise FileNotFoundError(
                "NCLOUD_ACCESS_KEY_ID 環境変数を設定してください。"
            )
        return ["-H", f"x-ncp-apigw-api-key: {ak}"]

    def exists(self, env_name: str, project: str) -> bool:
        try:
            headers = self._headers()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                *headers,
                f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"secretName"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        headers = self._headers()
        payload = json.dumps({
            "secretName": env_name,
            "secretValue": value,
        })
        # Try update
        result = subprocess.run(
            [
                "curl", "-s", "-X", "PUT",
                *headers,
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "error" not in result.stdout.lower():
            return True
        # Create
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                *headers,
                "-H", "Content-Type: application/json",
                "-d", payload,
                "https://secretmanager.apigw.ntruss.com/api/v1/secrets",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        try:
            headers = self._headers()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s", "-X", "DELETE",
                *headers,
                f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

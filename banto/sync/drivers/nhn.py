# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""NHN Cloud Secure Key Manager driver — uses REST API via curl."""
from __future__ import annotations

import json
import os
import subprocess

from .base import PlatformDriver


class NHNCloudDriver(PlatformDriver):
    """Deploy secrets to NHN Cloud Secure Key Manager.

    `project` is the appkey for the Key Manager service.
    Requires NHN_USER_ACCESS_KEY and NHN_SECRET_ACCESS_KEY env vars.
    """

    def _auth_headers(self) -> list[str]:
        ak = os.environ.get("NHN_USER_ACCESS_KEY", "")
        if not ak:
            raise FileNotFoundError(
                "NHN_USER_ACCESS_KEY 環境変数を設定してください。"
            )
        return ["-H", f"X-TC-AUTHENTICATION-ID: {ak}"]

    def exists(self, env_name: str, project: str) -> bool:
        try:
            headers = self._auth_headers()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                *headers,
                f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout)
            body = data.get("body", {})
            secrets = body if isinstance(body, list) else body.get("secrets", [])
            return any(s.get("name") == env_name for s in secrets)
        except (json.JSONDecodeError, TypeError):
            return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        headers = self._auth_headers()
        payload = json.dumps({
            "keyStoreName": "vault",
            "name": env_name,
            "secretValue": value,
        })
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                *headers,
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "error" not in result.stdout.lower()

    def delete(self, env_name: str, project: str) -> bool:
        # NHN requires key ID for deletion; need to list first
        try:
            headers = self._auth_headers()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                *headers,
                f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets",
            ],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
            body = data.get("body", {})
            secrets = body if isinstance(body, list) else body.get("secrets", [])
            for s in secrets:
                if s.get("name") == env_name:
                    key_id = s.get("keyId") or s.get("secretId")
                    if key_id:
                        del_result = subprocess.run(
                            [
                                "curl", "-s", "-X", "DELETE",
                                *headers,
                                f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/"
                                f"appkey/{project}/secrets/{key_id}",
                            ],
                            capture_output=True, text=True,
                        )
                        return del_result.returncode == 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

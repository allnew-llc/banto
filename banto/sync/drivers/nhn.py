# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""NHN Cloud Secure Key Manager driver — uses REST API via curl.

Security: JSON payloads containing secret values and auth tokens are
passed via stdin/tempfile to avoid exposure in `ps aux`. Auth headers use
curl -K - (config from stdin), and JSON bodies use -d @file.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .base import PlatformDriver


class NHNCloudDriver(PlatformDriver):
    """Deploy secrets to NHN Cloud Secure Key Manager.

    `project` is the appkey for the Key Manager service.
    Requires NHN_USER_ACCESS_KEY and NHN_SECRET_ACCESS_KEY env vars.
    """

    def _auth_key(self) -> str:
        ak = os.environ.get("NHN_USER_ACCESS_KEY", "")
        if not ak:
            raise FileNotFoundError(
                "NHN_USER_ACCESS_KEY 環境変数を設定してください。"
            )
        return ak

    def _curl_config(self, ak: str, content_type: bool = False) -> str:
        """Build curl config string with auth header."""
        config = f'-H "X-TC-AUTHENTICATION-ID: {ak}"\n'
        if content_type:
            config += '-H "Content-Type: application/json"\n'
        return config

    def exists(self, env_name: str, project: str) -> bool:
        try:
            ak = self._auth_key()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets"],
            input=self._curl_config(ak),
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
        # Security: pass auth via curl config (-K -) and JSON payload
        # via tempfile (-d @file) to avoid exposing secrets in argv.
        ak = self._auth_key()
        payload = json.dumps({
            "keyStoreName": "vault",
            "name": env_name,
            "secretValue": value,
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-nhn-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets"],
                input=self._curl_config(ak, content_type=True),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and "error" not in result.stdout.lower()
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        # NHN requires key ID for deletion; need to list first
        try:
            ak = self._auth_key()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/appkey/{project}/secrets"],
            input=self._curl_config(ak),
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
                            ["curl", "-s", "-X", "DELETE", "-K", "-",
                             f"https://api-keymanager.cloud.toast.com/keymanager/v1.0/"
                             f"appkey/{project}/secrets/{key_id}"],
                            input=self._curl_config(ak),
                            capture_output=True, text=True,
                        )
                        return del_result.returncode == 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

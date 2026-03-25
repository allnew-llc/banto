# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Naver Cloud Platform Secret Manager driver — uses REST API via curl.

Security: JSON payloads containing secret values and API keys are passed
via stdin/tempfile to avoid exposure in `ps aux`. Auth headers use
curl -K - (config from stdin), and JSON bodies use -d @file.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .base import PlatformDriver


class NaverCloudDriver(PlatformDriver):
    """Deploy secrets to Naver Cloud Platform Secret Manager.

    `project` is not used (secrets are account-scoped).
    Requires NCLOUD_ACCESS_KEY_ID and NCLOUD_SECRET_KEY environment variables.
    """

    def _api_key(self) -> str:
        ak = os.environ.get("NCLOUD_ACCESS_KEY_ID", "")
        if not ak:
            raise FileNotFoundError(
                "NCLOUD_ACCESS_KEY_ID 環境変数を設定してください。"
            )
        return ak

    def _curl_config(self, api_key: str, content_type: bool = False) -> str:
        """Build curl config string with auth header."""
        config = f'-H "x-ncp-apigw-api-key: {api_key}"\n'
        if content_type:
            config += '-H "Content-Type: application/json"\n'
        return config

    def exists(self, env_name: str, project: str) -> bool:
        try:
            api_key = self._api_key()
        except FileNotFoundError:
            return False
        # Security: pass API key via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}"],
            input=self._curl_config(api_key),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"secretName"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass API key via curl config (-K -) and JSON payload
        # via tempfile (-d @file) to avoid exposing secrets in argv.
        api_key = self._api_key()
        payload = json.dumps({
            "secretName": env_name,
            "secretValue": value,
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-naver-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            # Try update
            result = subprocess.run(
                ["curl", "-s", "-X", "PUT", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}"],
                input=self._curl_config(api_key, content_type=True),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "error" not in result.stdout.lower():
                return True
            # Create
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 "https://secretmanager.apigw.ntruss.com/api/v1/secrets"],
                input=self._curl_config(api_key, content_type=True),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        try:
            api_key = self._api_key()
        except FileNotFoundError:
            return False
        # Security: pass API key via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-X", "DELETE", "-K", "-",
             f"https://secretmanager.apigw.ntruss.com/api/v1/secrets/{env_name}"],
            input=self._curl_config(api_key),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

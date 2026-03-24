# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Sakura Cloud Secret Manager driver — uses REST API via curl.

Sakura Cloud Secret Manager is in beta. Uses the API directly
since usacloud doesn't have secret manager subcommands yet.

Security: JSON payloads containing secret values and auth credentials
are passed via stdin/tempfile to avoid exposure in `ps aux`.
Auth uses curl -K - (config from stdin) instead of -u in argv,
and JSON bodies use -d @file.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .base import PlatformDriver


class SakuraCloudDriver(PlatformDriver):
    """Deploy secrets to Sakura Cloud Secret Manager.

    `project` is the vault ID.
    Requires SAKURACLOUD_ACCESS_TOKEN and SAKURACLOUD_ACCESS_TOKEN_SECRET env vars.
    """

    def _auth(self) -> tuple[str, str]:
        token = os.environ.get("SAKURACLOUD_ACCESS_TOKEN", "")
        secret = os.environ.get("SAKURACLOUD_ACCESS_TOKEN_SECRET", "")
        if not token or not secret:
            raise FileNotFoundError(
                "SAKURACLOUD_ACCESS_TOKEN と SAKURACLOUD_ACCESS_TOKEN_SECRET "
                "環境変数を設定してください。"
            )
        return token, secret

    def _curl_config(self, token: str, secret: str,
                     content_type: bool = False) -> str:
        """Build curl config string with auth credentials."""
        config = f'-u "{token}:{secret}"\n'
        if content_type:
            config += '-H "Content-Type: application/json"\n'
        return config

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token, secret = self._auth()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-K", "-",
             f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
             f"vaults/{project}/secrets/{env_name}"],
            input=self._curl_config(token, secret),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"name"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass auth via curl config (-K -) and JSON payload
        # via tempfile (-d @file) to avoid exposing secrets in argv.
        token, secret = self._auth()
        payload = json.dumps({
            "name": env_name,
            "value": value,
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-sakura-", suffix=".json")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            # Try update
            result = subprocess.run(
                ["curl", "-s", "-X", "PUT", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                 f"vaults/{project}/secrets/{env_name}"],
                input=self._curl_config(token, secret, content_type=True),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "error" not in result.stdout.lower():
                return True
            # Create
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                 f"vaults/{project}/secrets"],
                input=self._curl_config(token, secret, content_type=True),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token, secret = self._auth()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config on stdin.
        result = subprocess.run(
            ["curl", "-s", "-X", "DELETE", "-K", "-",
             f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
             f"vaults/{project}/secrets/{env_name}"],
            input=self._curl_config(token, secret),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

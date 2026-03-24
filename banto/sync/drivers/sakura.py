# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Sakura Cloud Secret Manager driver — uses REST API via curl.

Sakura Cloud Secret Manager is in beta. Uses the API directly
since usacloud doesn't have secret manager subcommands yet.
"""
from __future__ import annotations

import json
import os
import subprocess

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

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token, secret = self._auth()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                "-u", f"{token}:{secret}",
                f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                f"vaults/{project}/secrets/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"name"' in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        token, secret = self._auth()
        payload = json.dumps({
            "name": env_name,
            "value": value,
        })
        # Try update
        result = subprocess.run(
            [
                "curl", "-s", "-X", "PUT",
                "-u", f"{token}:{secret}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                f"vaults/{project}/secrets/{env_name}",
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
                "-u", f"{token}:{secret}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                f"vaults/{project}/secrets",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token, secret = self._auth()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s", "-X", "DELETE",
                "-u", f"{token}:{secret}",
                f"https://secure.sakura.ad.jp/cloud/api/secretmanager/v1/"
                f"vaults/{project}/secrets/{env_name}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

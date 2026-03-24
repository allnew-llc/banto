# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Terraform Cloud / HCP Terraform variables driver — uses REST API.

Terraform Cloud manages workspace variables via the API.
Requires TFE_TOKEN (or TF_API_TOKEN) environment variable.
"""
from __future__ import annotations

import json
import os
import subprocess

from .base import PlatformDriver


def _token() -> str:
    """Get Terraform Cloud API token from env."""
    token = os.environ.get("TFE_TOKEN") or os.environ.get("TF_API_TOKEN", "")
    if not token:
        raise FileNotFoundError(
            "TFE_TOKEN または TF_API_TOKEN 環境変数を設定してください。"
        )
    return token


class TerraformCloudDriver(PlatformDriver):
    """Deploy secrets to Terraform Cloud workspace variables.

    `project` is in `org/workspace` format.
    """

    def _api_url(self, project: str) -> str:
        org, workspace = project.split("/", 1) if "/" in project else (project, "")
        return f"https://app.terraform.io/api/v2/organizations/{org}/workspaces/{workspace}/vars"

    def exists(self, env_name: str, project: str) -> bool:
        try:
            tok = _token()
        except FileNotFoundError:
            return False
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {tok}",
                "-H", "Content-Type: application/vnd.api+json",
                self._api_url(project),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout)
            return any(
                v.get("attributes", {}).get("key") == env_name
                for v in data.get("data", [])
            )
        except (json.JSONDecodeError, TypeError):
            return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        tok = _token()
        # Delete existing if present
        self._delete_by_key(env_name, project, tok)
        payload = json.dumps({
            "data": {
                "type": "vars",
                "attributes": {
                    "key": env_name,
                    "value": value,
                    "category": "env",
                    "sensitive": True,
                },
            }
        })
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", f"Authorization: Bearer {tok}",
                "-H", "Content-Type: application/vnd.api+json",
                "-d", payload,
                self._api_url(project),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"id"' in result.stdout

    def delete(self, env_name: str, project: str) -> bool:
        try:
            tok = _token()
        except FileNotFoundError:
            return False
        return self._delete_by_key(env_name, project, tok)

    def _delete_by_key(self, env_name: str, project: str, tok: str) -> bool:
        """Find and delete a variable by key name."""
        result = subprocess.run(
            [
                "curl", "-s",
                "-H", f"Authorization: Bearer {tok}",
                self._api_url(project),
            ],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(result.stdout)
            for v in data.get("data", []):
                if v.get("attributes", {}).get("key") == env_name:
                    var_id = v.get("id")
                    del_result = subprocess.run(
                        [
                            "curl", "-s", "-X", "DELETE",
                            "-H", f"Authorization: Bearer {tok}",
                            f"https://app.terraform.io/api/v2/vars/{var_id}",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    return del_result.returncode == 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

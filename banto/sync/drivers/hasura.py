# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Hasura Cloud env vars driver — uses REST API via curl."""
from __future__ import annotations

import json
import os
import subprocess

from .base import PlatformDriver


class HasuraCloudDriver(PlatformDriver):
    """Deploy secrets to Hasura Cloud project env vars.

    `project` is the Hasura Cloud project ID.
    Requires HASURA_CLOUD_ACCESS_TOKEN environment variable.
    """

    def _token(self) -> str:
        token = os.environ.get("HASURA_CLOUD_ACCESS_TOKEN", "")
        if not token:
            raise FileNotFoundError(
                "HASURA_CLOUD_ACCESS_TOKEN 環境変数を設定してください。"
            )
        return token

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        query = json.dumps({
            "query": f'''{{ projects_by_pk(id: "{project}") {{
                envs {{ hash_key }}
            }} }}'''
        })
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", f"Authorization: pat {token}",
                "-H", "Content-Type: application/json",
                "-d", query,
                "https://data.pro.hasura.io/v1/graphql",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and env_name in result.stdout

    def put(self, env_name: str, value: str, project: str) -> bool:
        token = self._token()
        mutation = json.dumps({
            "query": '''mutation($id: uuid!, $key: String!, $val: String!) {
                updateEnvVar(project_id: $id, hash_key: $key, hash_value: $val) {
                    hash_key
                }
            }''',
            "variables": {"id": project, "key": env_name, "val": value},
        })
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", f"Authorization: pat {token}",
                "-H", "Content-Type: application/json",
                "-d", mutation,
                "https://data.pro.hasura.io/v1/graphql",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "error" not in result.stdout.lower()

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        mutation = json.dumps({
            "query": '''mutation($id: uuid!, $key: String!) {
                deleteEnvVar(project_id: $id, hash_key: $key) {
                    hash_key
                }
            }''',
            "variables": {"id": project, "key": env_name},
        })
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", f"Authorization: pat {token}",
                "-H", "Content-Type: application/json",
                "-d", mutation,
                "https://data.pro.hasura.io/v1/graphql",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

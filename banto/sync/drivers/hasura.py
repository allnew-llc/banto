# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Hasura Cloud env vars driver — uses REST API via curl.

Security: JSON payloads containing secret values and auth tokens are
passed via stdin (curl -K - for headers, -d @file for body) to avoid
exposure in `ps aux`.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

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

    def _curl_config(self, token: str) -> str:
        """Build curl config string with auth and content-type headers."""
        return (
            f'-H "Authorization: pat {token}"\n'
            f'-H "Content-Type: application/json"\n'
        )

    def exists(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        # Security: pass auth and payload via stdin to avoid argv exposure.
        query = json.dumps({
            "query": f'''{{ projects_by_pk(id: "{project}") {{
                envs {{ hash_key }}
            }} }}'''
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-hasura-", suffix=".json")
        try:
            os.write(fd, query.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 "https://data.pro.hasura.io/v1/graphql"],
                input=self._curl_config(token),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and env_name in result.stdout
        finally:
            os.unlink(tmp_path)

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: pass auth via curl config (-K -) and mutation payload
        # via tempfile (-d @file) to avoid exposing secrets in argv.
        token = self._token()
        mutation = json.dumps({
            "query": '''mutation($id: uuid!, $key: String!, $val: String!) {
                updateEnvVar(project_id: $id, hash_key: $key, hash_value: $val) {
                    hash_key
                }
            }''',
            "variables": {"id": project, "key": env_name, "val": value},
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-hasura-", suffix=".json")
        try:
            os.write(fd, mutation.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 "https://data.pro.hasura.io/v1/graphql"],
                input=self._curl_config(token),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and "error" not in result.stdout.lower()
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        try:
            token = self._token()
        except FileNotFoundError:
            return False
        # Security: pass auth via curl config and payload via tempfile.
        mutation = json.dumps({
            "query": '''mutation($id: uuid!, $key: String!) {
                deleteEnvVar(project_id: $id, hash_key: $key) {
                    hash_key
                }
            }''',
            "variables": {"id": project, "key": env_name},
        })
        fd, tmp_path = tempfile.mkstemp(prefix="banto-hasura-", suffix=".json")
        try:
            os.write(fd, mutation.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "-K", "-",
                 "-d", f"@{tmp_path}",
                 "https://data.pro.hasura.io/v1/graphql"],
                input=self._curl_config(token),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

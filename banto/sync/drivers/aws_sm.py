# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""AWS Secrets Manager driver — uses `aws` CLI.

Unlike SSM Parameter Store, Secrets Manager uses create/update split.

Security: secret values are passed via stdin (--secret-string reads from
a tempfile written with 0o600 permissions) to avoid exposure in `ps aux`.
The `aws` CLI does not support reading --secret-string from stdin directly,
so we use a tempfile with restrictive permissions.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "aws CLI が見つかりません。brew install awscli でインストールしてください。"
)


def _find_aws() -> str:
    path = shutil.which("aws")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


def _write_secret_tempfile(value: str) -> str:
    """Write secret to a 0600 tempfile and return the path.

    Caller is responsible for deleting the file after use.
    """
    fd, path = tempfile.mkstemp(prefix="banto-secret-", suffix=".txt")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    return path


class AWSSecretsManagerDriver(PlatformDriver):
    """Deploy secrets to AWS Secrets Manager.

    `project` is used as a name prefix: <project>/<env_name>
    """

    def _secret_id(self, env_name: str, project: str) -> str:
        return f"{project}/{env_name}"

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_aws(), "secretsmanager", "describe-secret",
                    "--secret-id", self._secret_id(env_name, project),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: write value to a tempfile to avoid argv exposure in ps aux.
        # AWS CLI reads file:// URIs for --secret-string.
        aws = _find_aws()
        sid = self._secret_id(env_name, project)
        tmp_path = _write_secret_tempfile(value)
        try:
            # Try update first
            result = subprocess.run(
                [
                    aws, "secretsmanager", "put-secret-value",
                    "--secret-id", sid,
                    "--secret-string", f"file://{tmp_path}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
            # Secret doesn't exist — create it
            result = subprocess.run(
                [
                    aws, "secretsmanager", "create-secret",
                    "--name", sid,
                    "--secret-string", f"file://{tmp_path}",
                ],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        result = subprocess.run(
            [
                _find_aws(), "secretsmanager", "delete-secret",
                "--secret-id", self._secret_id(env_name, project),
                "--force-delete-without-recovery",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

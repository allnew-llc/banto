# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""AWS SSM Parameter Store driver — uses `aws` CLI.

Security: secret values are passed via a tempfile with 0o600 permissions
using the file:// URI scheme, avoiding exposure in `ps aux`.
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


class AWSSSMDriver(PlatformDriver):
    """Deploy secrets to AWS SSM Parameter Store.

    `project` is used as the path prefix: /<project>/<env_name>
    """

    def _param_name(self, env_name: str, project: str) -> str:
        return f"/{project}/{env_name}"

    def exists(self, env_name: str, project: str) -> bool:
        try:
            result = subprocess.run(
                [
                    _find_aws(), "ssm", "get-parameter",
                    "--name", self._param_name(env_name, project),
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: write value to a tempfile to avoid argv exposure in ps aux.
        # AWS CLI reads file:// URIs for --value.
        tmp_path = _write_secret_tempfile(value)
        try:
            result = subprocess.run(
                [
                    _find_aws(), "ssm", "put-parameter",
                    "--name", self._param_name(env_name, project),
                    "--value", f"file://{tmp_path}",
                    "--type", "SecureString",
                    "--overwrite",
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
                _find_aws(), "ssm", "delete-parameter",
                "--name", self._param_name(env_name, project),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

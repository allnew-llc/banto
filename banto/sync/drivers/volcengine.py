# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Volcengine (ByteDance) KMS driver — uses `ve` CLI.

Security: secret values are passed via a tempfile with 0o600 permissions
to avoid exposure in `ps aux`. The ve CLI doesn't support stdin for
--SecretValue, so we use a tempfile with file:// URI.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from .base import PlatformDriver


def _write_secret_tempfile(value: str) -> str:
    """Write secret to a 0600 tempfile and return the path.

    Caller is responsible for deleting the file after use.
    """
    fd, path = tempfile.mkstemp(prefix="banto-secret-", suffix=".txt")
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


class VolcengineKMSDriver(PlatformDriver):
    """Deploy secrets to Volcengine KMS.

    `project` is the region (e.g., `cn-beijing`).
    Uses VOLC_ACCESSKEY and VOLC_SECRETKEY environment variables.
    Falls back to `ve` CLI if available.
    """

    def exists(self, env_name: str, project: str) -> bool:
        import shutil

        ve = shutil.which("ve")
        if ve:
            try:
                result = subprocess.run(
                    [ve, "kms", "DescribeSecret", "--SecretName", env_name,
                     "--Region", project],
                    capture_output=True, text=True,
                )
                return result.returncode == 0
            except FileNotFoundError:
                pass
        return False

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: write value to a 0600 tempfile to avoid argv exposure.
        import shutil

        ve = shutil.which("ve")
        if ve:
            tmp_path = _write_secret_tempfile(value)
            try:
                # Try update
                result = subprocess.run(
                    [ve, "kms", "PutSecretValue", "--SecretName", env_name,
                     "--SecretValue", f"file://{tmp_path}", "--Region", project],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    return True
                # Create
                result = subprocess.run(
                    [ve, "kms", "CreateSecret", "--SecretName", env_name,
                     "--SecretValue", f"file://{tmp_path}", "--Region", project],
                    capture_output=True, text=True,
                )
                return result.returncode == 0
            finally:
                os.unlink(tmp_path)
        raise FileNotFoundError(
            "ve CLI が見つかりません。go install github.com/volcengine/volcengine-cli@latest "
            "でインストールしてください。"
        )

    def delete(self, env_name: str, project: str) -> bool:
        import shutil

        ve = shutil.which("ve")
        if not ve:
            return False
        result = subprocess.run(
            [ve, "kms", "DeleteSecret", "--SecretName", env_name,
             "--Region", project],
            capture_output=True, text=True,
        )
        return result.returncode == 0

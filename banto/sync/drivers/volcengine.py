# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Volcengine (ByteDance) KMS driver — uses REST API via curl."""
from __future__ import annotations

import subprocess

from .base import PlatformDriver


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
        import shutil

        ve = shutil.which("ve")
        if ve:
            # Try update
            result = subprocess.run(
                [ve, "kms", "PutSecretValue", "--SecretName", env_name,
                 "--SecretValue", value, "--Region", project],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return True
            # Create
            result = subprocess.run(
                [ve, "kms", "CreateSecret", "--SecretName", env_name,
                 "--SecretValue", value, "--Region", project],
                capture_output=True, text=True,
            )
            return result.returncode == 0
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

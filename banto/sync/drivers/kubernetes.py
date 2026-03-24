# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Kubernetes Secrets driver — uses `kubectl` CLI.

Security: secret values are passed via stdin to avoid exposure in `ps aux`.
Instead of --from-literal (which puts the value in argv), we generate the
YAML manifest with the value piped through stdin via --from-file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .base import PlatformDriver

_CLI_NOT_FOUND = (
    "kubectl が見つかりません。brew install kubectl でインストールしてください。"
)


def _find_kubectl() -> str:
    path = shutil.which("kubectl")
    if path is None:
        raise FileNotFoundError(_CLI_NOT_FOUND)
    return path


class KubernetesDriver(PlatformDriver):
    """Deploy secrets to Kubernetes cluster.

    `project` is in `namespace/secret-name` format.
    Creates or patches an Opaque Secret with the env_name as a key.
    """

    def _parse_project(self, project: str) -> tuple[str, str]:
        if "/" in project:
            ns, name = project.split("/", 1)
            return ns, name
        return "default", project

    def exists(self, env_name: str, project: str) -> bool:
        ns, secret_name = self._parse_project(project)
        try:
            result = subprocess.run(
                [
                    _find_kubectl(), "get", "secret", secret_name,
                    "-n", ns, "-o", f"jsonpath={{.data.{env_name}}}",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0 and result.stdout.strip() != ""

    def put(self, env_name: str, value: str, project: str) -> bool:
        # Security: write value to a 0600 tempfile and use --from-file
        # instead of --from-literal to avoid argv exposure in ps aux.
        kubectl = _find_kubectl()
        ns, secret_name = self._parse_project(project)
        fd, tmp_path = tempfile.mkstemp(prefix="banto-k8s-", suffix=".txt")
        try:
            os.write(fd, value.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, 0o600)
            # Generate YAML with --from-file (value in file, not argv)
            result = subprocess.run(
                [
                    kubectl, "create", "secret", "generic", secret_name,
                    "-n", ns,
                    f"--from-file={env_name}={tmp_path}",
                    "--dry-run=client", "-o", "yaml",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False
            # Apply (create or update)
            apply_result = subprocess.run(
                [kubectl, "apply", "-f", "-", "-n", ns],
                input=result.stdout,
                capture_output=True,
                text=True,
            )
            return apply_result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def delete(self, env_name: str, project: str) -> bool:
        kubectl = _find_kubectl()
        ns, secret_name = self._parse_project(project)
        # Remove a single key from the secret using patch
        result = subprocess.run(
            [
                kubectl, "patch", "secret", secret_name,
                "-n", ns, "--type=json",
                "-p", f'[{{"op":"remove","path":"/data/{env_name}"}}]',
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

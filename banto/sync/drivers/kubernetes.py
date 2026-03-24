# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Kubernetes Secrets driver — uses `kubectl` CLI."""
from __future__ import annotations

import shutil
import subprocess

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
        kubectl = _find_kubectl()
        ns, secret_name = self._parse_project(project)
        # Try to patch existing secret
        result = subprocess.run(
            [
                kubectl, "create", "secret", "generic", secret_name,
                "-n", ns,
                f"--from-literal={env_name}={value}",
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

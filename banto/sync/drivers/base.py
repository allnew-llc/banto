# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Abstract base class for platform drivers."""
from __future__ import annotations

from abc import ABC, abstractmethod


class PlatformDriver(ABC):
    """Interface for deploying secrets to a target platform."""

    @abstractmethod
    def exists(self, env_name: str, project: str) -> bool:
        """Check if the secret exists on the target."""

    @abstractmethod
    def put(self, env_name: str, value: str, project: str) -> bool:
        """Deploy a secret value to the target. Returns True on success."""

    @abstractmethod
    def delete(self, env_name: str, project: str) -> bool:
        """Remove a secret from the target. Returns True on success."""

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
keychain.py - macOS Keychain integration for API key storage.

Stores and retrieves API keys using the macOS `security` CLI tool.
Keys are stored as generic passwords in the login keychain.
"""

import os
import re
import subprocess

# Provider name validation: alphanumeric, hyphens, underscores only
_PROVIDER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_provider(provider: str) -> str:
    """Validate and normalize provider name for safe use in subprocess args."""
    provider = provider.strip()
    if not provider or not _PROVIDER_RE.match(provider):
        raise ValueError(
            f"Invalid provider name: {provider!r}. "
            "Use only letters, digits, hyphens, and underscores."
        )
    return provider.lower()


class KeyNotFoundError(Exception):
    """Raised when an API key is not found in Keychain."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"No API key found for provider '{provider}'. "
            f"Store it with: banto store {provider}"
        )


_PREFIX_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_prefix(prefix: str) -> str:
    """Validate service_prefix for safe use in Keychain service names.

    Raises:
        ValueError: If prefix contains characters outside [A-Za-z0-9._-].
    """
    prefix = prefix.strip()
    if not prefix or not _PREFIX_RE.match(prefix):
        raise ValueError(
            f"Invalid service_prefix: {prefix!r}. "
            "Use only letters, digits, dots, hyphens, and underscores."
        )
    return prefix


class KeychainStore:
    """macOS Keychain wrapper for API key storage."""

    DEFAULT_PREFIX = "banto"

    def __init__(self, *, service_prefix: str | None = None):
        """
        Args:
            service_prefix: Prefix for Keychain service names.
                            Default: "banto" (keys stored as "banto-openai" etc.)
        """
        self.prefix = _validate_prefix(service_prefix) if service_prefix else self.DEFAULT_PREFIX
        try:
            self.account = os.getlogin()
        except OSError:
            self.account = os.environ.get("USER", "unknown")
        self.keychain_path = os.path.expanduser(
            "~/Library/Keychains/login.keychain-db"
        )

    def _service_name(self, provider: str) -> str:
        provider = _validate_provider(provider)
        return f"{self.prefix}-{provider}"

    def get(self, provider: str) -> str | None:
        """Retrieve an API key from Keychain. Returns None if not found."""
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", self._service_name(provider),
                    "-a", self.account,
                    "-w",
                    self.keychain_path,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.SubprocessError, OSError):
            return None

    def store(self, provider: str, api_key: str) -> bool:
        """Store an API key in Keychain. Overwrites existing.

        Note: The macOS ``security`` CLI requires the key as a command-line
        argument (``-w <value>``), which is briefly visible in the process
        table. This is a limitation of the ``security`` tool, not of banto.
        The key is stored securely in Keychain once the command completes.
        """
        service = self._service_name(provider)
        try:
            # Delete existing (ignore errors)
            subprocess.run(
                [
                    "security", "delete-generic-password",
                    "-s", service, "-a", self.account,
                    self.keychain_path,
                ],
                capture_output=True,
            )
            # SECURITY NOTE: API key is briefly visible in process table via `ps aux`.
            # macOS `security` CLI does not reliably support stdin for -w flag.
            # See README.md Security > Threat Model for mitigation guidance.
            result = subprocess.run(
                [
                    "security", "add-generic-password",
                    "-s", service, "-a", self.account,
                    "-w", api_key,
                    self.keychain_path,
                ],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def delete(self, provider: str) -> bool:
        """Delete an API key from Keychain."""
        try:
            result = subprocess.run(
                [
                    "security", "delete-generic-password",
                    "-s", self._service_name(provider),
                    "-a", self.account,
                    self.keychain_path,
                ],
                capture_output=True,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def exists(self, provider: str) -> bool:
        """Check if an API key exists in Keychain (without retrieving it)."""
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", self._service_name(provider),
                    "-a", self.account,
                    self.keychain_path,
                ],
                capture_output=True,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def list_providers(self, known_providers: list[str]) -> list[str]:
        """List which of the known providers have stored keys."""
        return [p for p in known_providers if self.exists(p)]

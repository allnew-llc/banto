"""
keychain.py - macOS Keychain integration for API key storage.

Stores and retrieves API keys using the macOS `security` CLI tool.
Keys are stored as generic passwords in the login keychain.
"""

import os
import subprocess


class KeyNotFoundError(Exception):
    """Raised when an API key is not found in Keychain."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"No API key found for provider '{provider}'. "
            f"Store it with: banto store {provider}"
        )


class KeychainStore:
    """macOS Keychain wrapper for API key storage."""

    DEFAULT_PREFIX = "banto"

    def __init__(self, *, service_prefix: str | None = None):
        """
        Args:
            service_prefix: Prefix for Keychain service names.
                            Default: "banto" (keys stored as "banto-openai" etc.)
        """
        self.prefix = service_prefix or self.DEFAULT_PREFIX
        result = subprocess.run(
            ["whoami"], capture_output=True, text=True, check=True
        )
        self.account = result.stdout.strip()
        self.keychain_path = os.path.expanduser(
            "~/Library/Keychains/login.keychain-db"
        )

    def _service_name(self, provider: str) -> str:
        return f"{self.prefix}-{provider.lower()}"

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
        """Store an API key in Keychain. Overwrites existing."""
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
        """Check if an API key exists in Keychain."""
        return self.get(provider) is not None

    def list_providers(self, known_providers: list[str]) -> list[str]:
        """List which of the known providers have stored keys."""
        return [p for p in known_providers if self.exists(p)]

"""
backend.py - Pluggable secret storage protocol.

Any object that implements these five methods can be used as a
secret backend for SecureVault. No inheritance required (structural typing).

Built-in backend:
    KeychainStore (macOS Keychain, default)

See examples/06_custom_backend.py for 1Password, env-var, and
in-memory implementations.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretBackend(Protocol):
    """Protocol for secret storage backends.

    Implement these methods to plug in any secret store
    (1Password, HashiCorp Vault, AWS Secrets Manager, etc.).
    """

    def get(self, provider: str) -> str | None:
        """Retrieve an API key. Returns None if not found."""
        ...

    def store(self, provider: str, api_key: str) -> bool:
        """Store an API key. Returns True on success."""
        ...

    def delete(self, provider: str) -> bool:
        """Delete an API key. Returns True on success."""
        ...

    def exists(self, provider: str) -> bool:
        """Check if an API key exists for the given provider."""
        ...

    def list_providers(self, known_providers: list[str]) -> list[str]:
        """Return which of the known providers have stored keys."""
        ...

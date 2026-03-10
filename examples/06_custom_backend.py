#!/usr/bin/env python3
"""
Example 6: Custom secret storage backends.

banto's secret storage is pluggable via the SecretBackend protocol.
Any object with get/store/delete/exists/list_providers methods works.
No inheritance required.

This file shows three alternative backends:
  1. EnvVarBackend     - reads keys from environment variables
  2. OnePasswordBackend - uses 1Password CLI (op)
  3. InMemoryBackend    - in-memory store for testing

Usage:
    # With environment variables
    export BANTO_KEY_OPENAI="sk-..."
    python examples/06_custom_backend.py

    # Or with 1Password
    python examples/06_custom_backend.py --1password
"""

import os
import subprocess


# ---------------------------------------------------------------------------
# Backend 1: Environment Variables
# ---------------------------------------------------------------------------

class EnvVarBackend:
    """Read API keys from environment variables.

    Keys are expected as BANTO_KEY_<PROVIDER> (e.g. BANTO_KEY_OPENAI).
    store() and delete() modify the current process environment only.
    """

    def __init__(self, prefix: str = "BANTO_KEY"):
        self.prefix = prefix

    def _env_name(self, provider: str) -> str:
        return f"{self.prefix}_{provider.upper()}"

    def get(self, provider: str) -> str | None:
        return os.environ.get(self._env_name(provider))

    def store(self, provider: str, api_key: str) -> bool:
        os.environ[self._env_name(provider)] = api_key
        return True

    def delete(self, provider: str) -> bool:
        key = self._env_name(provider)
        if key in os.environ:
            del os.environ[key]
            return True
        return False

    def exists(self, provider: str) -> bool:
        return self._env_name(provider) in os.environ

    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]


# ---------------------------------------------------------------------------
# Backend 2: 1Password CLI
# ---------------------------------------------------------------------------

class OnePasswordBackend:
    """Retrieve API keys from 1Password using the `op` CLI.

    Prerequisites:
        brew install 1password-cli
        op signin

    Keys are stored as items in the specified vault with the title
    "banto-<provider>" (e.g. "banto-openai").
    """

    def __init__(self, vault: str = "Private"):
        self.vault = vault

    def _item_name(self, provider: str) -> str:
        return f"banto-{provider.lower()}"

    def get(self, provider: str) -> str | None:
        try:
            result = subprocess.run(
                [
                    "op", "item", "get", self._item_name(provider),
                    "--vault", self.vault,
                    "--fields", "label=credential",
                    "--format", "json",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return data.get("value")
            return None
        except (subprocess.SubprocessError, OSError):
            return None

    def store(self, provider: str, api_key: str) -> bool:
        name = self._item_name(provider)
        try:
            # Try to update existing item first
            result = subprocess.run(
                [
                    "op", "item", "edit", name,
                    "--vault", self.vault,
                    f"credential={api_key}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True

            # Create new item
            result = subprocess.run(
                [
                    "op", "item", "create",
                    "--category", "api_credential",
                    "--title", name,
                    "--vault", self.vault,
                    f"credential={api_key}",
                ],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def delete(self, provider: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "op", "item", "delete", self._item_name(provider),
                    "--vault", self.vault,
                ],
                capture_output=True,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def exists(self, provider: str) -> bool:
        return self.get(provider) is not None

    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]


# ---------------------------------------------------------------------------
# Backend 3: In-Memory (for testing)
# ---------------------------------------------------------------------------

class InMemoryBackend:
    """In-memory secret store. Useful for testing and CI.

    Keys exist only for the lifetime of the process.
    """

    def __init__(self, initial: dict[str, str] | None = None):
        self._store: dict[str, str] = dict(initial) if initial else {}

    def get(self, provider: str) -> str | None:
        return self._store.get(provider)

    def store(self, provider: str, api_key: str) -> bool:
        self._store[provider] = api_key
        return True

    def delete(self, provider: str) -> bool:
        if provider in self._store:
            del self._store[provider]
            return True
        return False

    def exists(self, provider: str) -> bool:
        return provider in self._store

    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if p in self._store]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def main():
    import sys

    from banto import SecureVault, BudgetExceededError, KeyNotFoundError

    # Choose backend based on CLI argument
    if "--1password" in sys.argv:
        print("Using 1Password backend\n")
        backend = OnePasswordBackend(vault="Private")
    else:
        print("Using environment variable backend")
        print("  Set BANTO_KEY_OPENAI=<your-key> to test\n")
        backend = EnvVarBackend()

    # Pass custom backend to SecureVault
    vault = SecureVault(caller="custom-backend-demo", backend=backend)

    try:
        key = vault.get_key(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
        )
        # Mask the key for display
        print(f"Key retrieved: {key[:8]}...{key[-4:]}")

        status = vault.get_budget_status()
        print(f"Budget: ${status['used_usd']:.2f} / ${status['monthly_limit_usd']:.2f}")

    except BudgetExceededError as e:
        print(f"Budget exceeded: ${e.remaining:.2f} remaining")

    except KeyNotFoundError as e:
        print(f"No key found for '{e.provider}'")
        if isinstance(backend, EnvVarBackend):
            print(f"  Set {backend._env_name(e.provider)}=<your-key>")


if __name__ == "__main__":
    main()

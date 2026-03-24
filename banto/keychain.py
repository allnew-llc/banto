# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
keychain.py - macOS Keychain integration for API key storage.

Uses the macOS Security framework via ctypes for store/get operations.
Secret values NEVER appear in process arguments, environment variables,
or temporary files. The only path for secret data is:
  Python memory → Security framework → Keychain (encrypted).

Delete/exists use the `security` CLI since they don't handle values.
"""

import ctypes
import ctypes.util
import os
import re
import subprocess

# Provider name validation: alphanumeric, hyphens, underscores only
_PROVIDER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_provider(provider: str) -> str:
    """Validate and normalize provider name for safe use."""
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
    """Validate service_prefix for safe use in Keychain service names."""
    prefix = prefix.strip()
    if not prefix or not _PREFIX_RE.match(prefix):
        raise ValueError(
            f"Invalid service_prefix: {prefix!r}. "
            "Use only letters, digits, dots, hyphens, and underscores."
        )
    return prefix


# --- macOS Security framework via ctypes ---

def _load_security_framework():
    """Load the macOS Security framework. Returns None on non-macOS."""
    lib_path = ctypes.util.find_library("Security")
    if lib_path is None:
        return None
    try:
        return ctypes.cdll.LoadLibrary(lib_path)
    except OSError:
        return None


_SECURITY_LIB = _load_security_framework()


def _ctypes_store(service: str, account: str, password: str) -> bool:
    """Store a password via Security framework. No argv exposure."""
    if _SECURITY_LIB is None:
        return False
    svc = service.encode("utf-8")
    acct = account.encode("utf-8")
    pwd = password.encode("utf-8")

    status = _SECURITY_LIB.SecKeychainAddGenericPassword(
        None,
        len(svc), svc,
        len(acct), acct,
        len(pwd), pwd,
        None,
    )
    if status == 0:
        return True
    if status == -25299:  # errSecDuplicateItem — update existing
        item_ref = ctypes.c_void_p()
        find_status = _SECURITY_LIB.SecKeychainFindGenericPassword(
            None,
            len(svc), svc,
            len(acct), acct,
            None, None,
            ctypes.byref(item_ref),
        )
        if find_status != 0:
            return False
        mod_status = _SECURITY_LIB.SecKeychainItemModifyAttributesAndData(
            item_ref, None, len(pwd), pwd,
        )
        _SECURITY_LIB.CFRelease(item_ref)
        return mod_status == 0
    return False


def _ctypes_get(service: str, account: str) -> str | None:
    """Retrieve a password via Security framework. No argv exposure."""
    if _SECURITY_LIB is None:
        return None
    svc = service.encode("utf-8")
    acct = account.encode("utf-8")

    pwd_length = ctypes.c_uint32()
    pwd_data = ctypes.c_void_p()
    item_ref = ctypes.c_void_p()

    status = _SECURITY_LIB.SecKeychainFindGenericPassword(
        None,
        len(svc), svc,
        len(acct), acct,
        ctypes.byref(pwd_length),
        ctypes.byref(pwd_data),
        ctypes.byref(item_ref),
    )
    if status != 0:
        return None

    password = ctypes.string_at(pwd_data, pwd_length.value).decode("utf-8")

    # Free the password buffer allocated by Security framework
    _SECURITY_LIB.SecKeychainItemFreeContent(None, pwd_data)
    if item_ref:
        _SECURITY_LIB.CFRelease(item_ref)

    return password


class KeychainStore:
    """macOS Keychain wrapper for API key storage.

    Uses the Security framework (ctypes) for store/get — secret values
    never appear in process arguments, env vars, or temp files.
    """

    DEFAULT_PREFIX = "banto"

    def __init__(self, *, service_prefix: str | None = None):
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
        return _ctypes_get(self._service_name(provider), self.account)

    def store(self, provider: str, api_key: str) -> bool:
        """Store an API key in Keychain. Overwrites existing.

        Uses macOS Security framework directly — the secret value
        never appears in process arguments or temporary files.
        """
        return _ctypes_store(self._service_name(provider), self.account, api_key)

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

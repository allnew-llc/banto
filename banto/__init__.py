# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
banto - Local-first secret management for developers.

Named after the bantō (番頭), the head clerk of Edo-period merchant houses
who held the keys to the storehouse and managed the books.

Core: secure key storage via macOS Keychain with multi-platform sync.
Optional modules:
  - budget: LLM cost gating with hold/settle pattern
  - lease: dynamic secrets with TTL and auto-revocation

Default backend: macOS Keychain. Custom backends (1Password, env vars, etc.)
can be plugged in via the SecretBackend protocol.
"""

from .backend import SecretBackend
from .guard import CostGuard, BudgetExceededError
from .keychain import KeychainStore, KeyNotFoundError
from .profiles import ProfileManager
from .vault import SecureVault

__all__ = [
    "SecureVault",
    "SecretBackend",
    "CostGuard",
    "BudgetExceededError",
    "KeychainStore",
    "KeyNotFoundError",
    "ProfileManager",
]
__version__ = "5.1.0"

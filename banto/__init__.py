"""
banto - Budget-gated API key vault for LLM applications.

Named after the bantō (番頭), the head clerk of Edo-period merchant houses
who held the keys to the storehouse and managed the books.

Combines pluggable secret storage with monthly budget enforcement.
API keys are only accessible when the budget allows.
Default backend: macOS Keychain. Custom backends (1Password, env vars, etc.)
can be plugged in via the SecretBackend protocol.
"""

from .backend import SecretBackend
from .guard import CostGuard, BudgetExceededError
from .keychain import KeychainStore, KeyNotFoundError
from .vault import SecureVault

__all__ = [
    "SecureVault",
    "SecretBackend",
    "CostGuard",
    "BudgetExceededError",
    "KeychainStore",
    "KeyNotFoundError",
]
__version__ = "2.1.0"

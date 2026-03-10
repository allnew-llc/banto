"""
banto - Budget-gated API key vault for LLM applications.

Named after the bantō (番頭), the head clerk of Edo-period merchant houses
who held the keys to the storehouse and managed the books.

Combines macOS Keychain secret storage with monthly budget enforcement.
API keys are only accessible when the budget allows.
"""

from .guard import CostGuard, BudgetExceededError
from .keychain import KeychainStore, KeyNotFoundError
from .vault import SecureVault

__all__ = [
    "SecureVault",
    "CostGuard",
    "BudgetExceededError",
    "KeychainStore",
    "KeyNotFoundError",
]
__version__ = "2.0.0"

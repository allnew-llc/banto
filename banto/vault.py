"""
vault.py - SecureVault: budget-gated API key access.

The core design: API keys are only retrievable when budget allows.
No budget = no key = no API call possible. Bypass-proof by construction.

Uses a hold/settle pattern for pessimistic budget reservation:
- get_key() reserves (holds) the estimated cost upfront
- record_usage() settles the hold with actual cost, freeing any surplus
- If record_usage() is never called, the hold stays — safe-side bias
"""

import threading

from .backend import SecretBackend
from .guard import CostGuard, BudgetExceededError
from .keychain import KeychainStore, KeyNotFoundError


class SecureVault:
    """
    Budget-gated API key vault.

    Combines secret storage with monthly budget enforcement.
    ``get_key()`` reserves budget and retrieves the key atomically.
    ``record_usage()`` settles the reservation with actual cost.

    The secret backend is pluggable. By default, macOS Keychain is used.
    Pass ``backend=`` to use 1Password, env vars, or any custom store.

    Usage:
        vault = SecureVault(caller="my_mcp")

        # Or with a custom backend:
        vault = SecureVault(caller="my_mcp", backend=my_1password_backend)

        # Main loop: get_key() holds budget, record_usage() settles
        key = vault.get_key(model="gpt-4o",
                            input_tokens=1000, output_tokens=500)
        response = openai.chat.completions.create(..., api_key=key)

        # Settle with actual usage (frees surplus budget)
        vault.record_usage(model="gpt-4o",
                           input_tokens=800, output_tokens=400,
                           provider="openai", operation="chat")
    """

    def __init__(
        self,
        caller: str = "unknown",
        *,
        backend: SecretBackend | None = None,
        config_path: str | None = None,
        data_dir: str | None = None,
        keychain_prefix: str | None = None,
    ):
        """
        Args:
            caller: Identifier for the service using this vault.
            backend: Secret storage backend. Defaults to macOS KeychainStore.
                     Any object implementing SecretBackend protocol works.
            config_path: Path to config.json (optional override).
            data_dir: Path to usage data directory (optional override).
            keychain_prefix: Keychain service name prefix (default: "banto").
                             Ignored when backend is provided.
        """
        self._guard = CostGuard(
            config_path=config_path, caller=caller, data_dir=data_dir
        )
        self._backend: SecretBackend = backend or KeychainStore(
            service_prefix=keychain_prefix
        )
        self._provider_map = self._build_provider_map()
        self._holds_lock = threading.Lock()
        self._pending_holds: dict[str, str] = {}  # "model:provider" -> hold_id

    def _build_provider_map(self) -> dict[str, str]:
        """Build model -> provider mapping from config."""
        mapping: dict[str, str] = {}
        for provider, info in self._guard.providers.items():
            for model in info.get("models", []):
                mapping[model] = provider
        return mapping

    def _resolve_provider(self, provider: str | None, model: str) -> str:
        if provider:
            return provider
        if model in self._provider_map:
            return self._provider_map[model]
        raise ValueError(
            f"Cannot determine provider for model '{model}'. "
            f"Pass provider= explicitly or add the model to config.json providers."
        )

    # --- Key management ---

    def store_key(self, provider: str, api_key: str) -> bool:
        """Store an API key in Keychain."""
        return self._backend.store(provider, api_key)

    def delete_key(self, provider: str) -> bool:
        """Delete an API key from Keychain."""
        return self._backend.delete(provider)

    def has_key(self, provider: str) -> bool:
        """Check if a provider has a stored key."""
        return self._backend.exists(provider)

    def list_providers(self) -> list[str]:
        """List providers that have stored keys."""
        known = sorted(set(self._provider_map.values()))
        return self._backend.list_providers(known)

    # --- Core: budget-gated key access ---

    def get_key(
        self,
        *,
        model: str,
        provider: str | None = None,
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> str:
        """
        Get an API key, gated by budget hold.

        Reserves the estimated cost in the usage log (pessimistic hold),
        then retrieves the key from Keychain. If record_usage() is called
        later, the hold is settled with actual cost. If not, the hold
        amount stays reserved — budget errs on the safe side.

        Args:
            model: Model name (e.g. "gpt-4o", "dall-e-3").
            provider: Provider name. Auto-resolved from model if omitted.
            (remaining args): Cost estimation parameters.

        Returns:
            The API key string.

        Raises:
            BudgetExceededError: Estimated cost exceeds remaining budget.
            KeyNotFoundError: No key stored for the provider.
            ValueError: Provider cannot be determined from model name.
        """
        resolved = self._resolve_provider(provider, model)

        # Step 1: budget hold (raises BudgetExceededError if over)
        hold_id = self._guard.hold_budget(
            model=model,
            provider=resolved,
            n=n,
            seconds=seconds,
            quality=quality,
            size=size,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        # Step 2: key retrieval (only reached if budget allows)
        key = self._backend.get(resolved)
        if key is None:
            raise KeyNotFoundError(resolved)

        # Track hold for later settlement
        hold_key = f"{model}:{resolved}"
        with self._holds_lock:
            self._pending_holds[hold_key] = hold_id

        return key

    # --- Usage recording ---

    def record_usage(
        self,
        model: str,
        *,
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider: str = "",
        operation: str = "",
    ) -> dict:
        """
        Record actual usage after a successful API call.

        If a matching hold exists (from get_key()), settles it with
        actual cost, freeing any surplus budget. Otherwise falls back
        to a direct usage record.
        """
        resolved = provider or self._provider_map.get(model, "")
        hold_key = f"{model}:{resolved}"

        # Pop matching hold
        hold_id: str | None = None
        with self._holds_lock:
            hold_id = self._pending_holds.pop(hold_key, None)

        if hold_id:
            return self._guard.settle_hold(
                hold_id,
                model=model,
                n=n,
                seconds=seconds,
                quality=quality,
                size=size,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=resolved,
                operation=operation,
            )

        # No hold found — direct record (backward compatible)
        return self._guard.record_usage(
            model=model,
            n=n,
            seconds=seconds,
            quality=quality,
            size=size,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=resolved,
            operation=operation,
        )

    # --- Budget queries ---

    def get_budget_status(self) -> dict:
        """Get current month's budget status."""
        return self._guard.get_remaining_budget()

    def estimate_cost(self, model: str, **kwargs) -> float:
        """Estimate cost without recording or checking budget."""
        return self._guard.estimate_cost(model=model, **kwargs)

    def set_budget(self, **kwargs) -> None:
        """Update budget limits. See CostGuard.set_budget() for args."""
        self._guard.set_budget(**kwargs)

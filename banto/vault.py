# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
vault.py - SecureVault: modular API key access with optional budget gating.

Core: secure key storage and retrieval via pluggable backends.
Optional budget module: hold/settle pattern for pessimistic cost reservation.

When budget=True:
- get_key() reserves (holds) the estimated cost upfront
- record_usage() settles the hold with actual cost, freeing any surplus
- If record_usage() is never called, the hold stays — safe-side bias

When budget=False (default):
- get_key() returns the key directly without cost checks
- record_usage() and budget methods are no-ops
"""

import json
import threading
from pathlib import Path

from .backend import SecretBackend
from .guard import CostGuard, BudgetExceededError
from .keychain import KeychainStore, KeyNotFoundError
from .profiles import ProfileManager


class SecureVault:
    """
    Modular API key vault with optional budget gating.

    Core functionality: secure key storage and retrieval.
    Enable ``budget=True`` to add LLM cost control (hold/settle pattern).

    The secret backend is pluggable. By default, macOS Keychain is used.
    Pass ``backend=`` to use 1Password, env vars, or any custom store.

    Usage:
        # Simple mode — no budget, just key management:
        vault = SecureVault()
        key = vault.get_key(provider="openai")

        # Budget mode — cost-gated access:
        vault = SecureVault(budget=True, caller="my_mcp")
        key = vault.get_key(model="gpt-4o",
                            input_tokens=1000, output_tokens=500)
        vault.record_usage(model="gpt-4o",
                           input_tokens=800, output_tokens=400,
                           provider="openai", operation="chat")

        # Role-based model resolution via profiles:
        key = vault.get_key(role="chat",
                            input_tokens=1000, output_tokens=500)
    """

    def __init__(
        self,
        caller: str = "unknown",
        *,
        budget: bool | None = None,
        backend: SecretBackend | None = None,
        config_path: str | None = None,
        data_dir: str | None = None,
        keychain_prefix: str | None = None,
    ):
        """
        Args:
            caller: Identifier for the service using this vault.
            budget: Enable budget gating. None = auto-detect from config
                    (enabled if monthly_limit_usd > 0). False = disabled.
                    True = enabled (requires config with limits).
            backend: Secret storage backend. Defaults to macOS KeychainStore.
                     Any object implementing SecretBackend protocol works.
            config_path: Path to config.json (optional override).
            data_dir: Path to usage data directory (optional override).
            keychain_prefix: Keychain service name prefix (default: "banto").
                             Ignored when backend is provided.
        """
        self._backend: SecretBackend = backend or KeychainStore(
            service_prefix=keychain_prefix
        )

        # Budget: lazy init — only create CostGuard when needed
        self._guard: CostGuard | None = None
        self._provider_map: dict[str, str] = {}
        self._holds_lock = threading.Lock()
        self._pending_holds: dict[str, list[str]] = {}
        self._profile_manager: ProfileManager | None = None
        self._config_path = config_path
        self._data_dir = data_dir
        self._caller = caller

        # Determine budget mode
        if budget is True:
            self._init_budget()
        elif budget is None:
            # Auto-detect: enable if config exists with monthly_limit > 0
            self._try_auto_budget()
        # budget=False: leave _guard as None

    def _init_budget(self) -> None:
        """Initialize budget subsystem (CostGuard + profiles)."""
        self._guard = CostGuard(
            config_path=self._config_path, caller=self._caller,
            data_dir=self._data_dir,
        )
        self._provider_map = self._build_provider_map()
        with open(self._guard.config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        self._profile_manager = ProfileManager(config_data)

    def _try_auto_budget(self) -> None:
        """Auto-detect budget mode from config."""
        from .guard import CONFIG_DIR
        cfg_path = Path(self._config_path) if self._config_path else CONFIG_DIR / "config.json"
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if data.get("monthly_limit_usd", 0) > 0:
                    self._init_budget()
            except (json.JSONDecodeError, OSError):
                pass

    @property
    def budget_enabled(self) -> bool:
        """Whether budget gating is active."""
        return self._guard is not None

    def _build_provider_map(self) -> dict[str, str]:
        """Build model -> provider mapping from config."""
        if self._guard is None:
            return {}
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
        model: str | None = None,
        role: str | None = None,
        provider: str | None = None,
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> str:
        """
        Get an API key, optionally gated by budget hold.

        When budget is enabled: reserves estimated cost before returning key.
        When budget is disabled: returns key directly by provider name.

        Args:
            model: Model name (e.g. "gpt-4o"). Required when budget enabled.
            role: Task role (e.g. "chat"). Resolved via active profile.
            provider: Provider name. Required when budget disabled and
                      model is not specified. Auto-resolved from model
                      when budget enabled.
            (remaining args): Cost estimation parameters (budget mode only).

        Returns:
            The API key string.

        Raises:
            BudgetExceededError: Budget enabled and cost exceeds limit.
            KeyNotFoundError: No key stored for the provider.
            ValueError: Cannot determine which key to retrieve.
        """
        # --- No budget: simple key retrieval ---
        if self._guard is None:
            if provider:
                key = self._backend.get(provider)
                if key is None:
                    raise KeyNotFoundError(provider)
                return key
            if model:
                resolved = self._provider_map.get(model, model)
                key = self._backend.get(resolved)
                if key is None:
                    raise KeyNotFoundError(resolved)
                return key
            raise ValueError(
                "Specify 'provider' or 'model' to retrieve a key"
            )

        # --- Budget mode: hold/settle pattern ---
        if model is None and role is None:
            raise ValueError("Either 'model' or 'role' must be specified")
        if model is None:
            assert role is not None
            assert self._profile_manager is not None
            model = self._profile_manager.resolve_model(role)

        assert model is not None
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
            self._guard.void_hold(hold_id)
            raise KeyNotFoundError(resolved)

        # Track hold for later settlement
        hold_key = f"{model}:{resolved}"
        with self._holds_lock:
            self._pending_holds.setdefault(hold_key, []).append(hold_id)

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
        to a direct usage record. No-op when budget is disabled.
        """
        if self._guard is None:
            return {"budget_enabled": False}
        resolved = provider or self._provider_map.get(model, provider)
        hold_key = f"{model}:{resolved}"

        # Pop matching hold (FIFO)
        hold_id: str | None = None
        with self._holds_lock:
            holds = self._pending_holds.get(hold_key, [])
            if holds:
                hold_id = holds.pop(0)
                if not holds:
                    del self._pending_holds[hold_key]

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

    # --- Budget queries (no-ops when budget disabled) ---

    def get_budget_status(self) -> dict:
        """Get current month's budget status. Returns empty dict if budget disabled."""
        if self._guard is None:
            return {"budget_enabled": False}
        status = self._guard.get_remaining_budget()
        status["budget_enabled"] = True
        return status

    def estimate_cost(self, model: str, **kwargs) -> float:
        """Estimate cost without recording or checking budget. Returns 0 if budget disabled."""
        if self._guard is None:
            return 0.0
        return self._guard.estimate_cost(model=model, **kwargs)

    def set_budget(self, **kwargs) -> None:
        """Update budget limits. Initializes budget subsystem if not already active."""
        if self._guard is None:
            self._init_budget()
        assert self._guard is not None
        self._guard.set_budget(**kwargs)

    # --- Profile management ---

    def set_profile(self, name: str) -> None:
        """Switch the active model profile.

        Args:
            name: Profile name (e.g. "quality", "balanced", "budget").

        Raises:
            ValueError: If name is not a known profile.
        """
        self._profile_manager.active_profile = name

    def get_profiles(self) -> dict[str, dict]:
        """Return all profiles with active indicator.

        Returns:
            Dict mapping profile name to {"models": {...}, "active": bool}.
        """
        return self._profile_manager.list_profiles()

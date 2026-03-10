"""
guard.py - API cost tracking and monthly budget enforcement.

No external dependencies beyond Python 3.10+ stdlib.
"""

import json
import os
import sys
import threading
import uuid
from pathlib import Path
from datetime import datetime

CONFIG_DIR = Path.home() / ".config" / "banto"

# Cross-platform file locking (macOS primary, Windows fallback)
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f, exclusive: bool = False) -> None:
        msvcrt.locking(
            f.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_LOCK, 1
        )

    def _unlock_file(f) -> None:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_file(f, exclusive: bool = False) -> None:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)

    def _unlock_file(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


class BudgetExceededError(Exception):
    """Raised when an API call would exceed a budget limit."""

    def __init__(
        self,
        requested: float,
        remaining: float,
        limit: float,
        scope: str = "global",
        scope_name: str = "",
    ):
        self.requested = requested
        self.remaining = remaining
        self.limit = limit
        self.scope = scope
        self.scope_name = scope_name
        if scope == "global":
            label = "monthly limit"
        else:
            label = f"{scope} '{scope_name}' limit"
        super().__init__(
            f"Budget exceeded ({label}): requested ${requested:.3f}, "
            f"remaining ${remaining:.3f} of ${limit:.2f}"
        )


def _resolve_config_path(config_path: str | None) -> Path:
    """Resolve config path: explicit > user override > bundled default."""
    if config_path:
        return Path(config_path)
    user_config = CONFIG_DIR / "config.json"
    if user_config.exists():
        return user_config
    return Path(__file__).parent / "config.json"


class CostGuard:
    """
    API cost tracker with monthly budget enforcement.

    Usage:
        guard = CostGuard(caller="my_mcp_server")
        guard.check_budget(model="dall-e-3", quality="standard", size="1024x1024")
        guard.record_usage(model="dall-e-3", quality="standard", size="1024x1024",
                          provider="openai", operation="image")
    """

    def __init__(
        self,
        config_path: str | None = None,
        caller: str = "unknown",
        data_dir: str | None = None,
    ):
        self._lock = threading.Lock()
        self.caller = caller
        self.config_path = _resolve_config_path(config_path)

        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.monthly_limit_usd: float = config["monthly_limit_usd"]
        self.provider_limits: dict[str, float] = config.get("provider_limits", {})
        self.model_limits: dict[str, float] = config.get("model_limits", {})
        self.pricing: dict = config["pricing"]
        self.providers: dict = config.get("providers", {})

        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = CONFIG_DIR / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_usage_file_path(self) -> Path:
        now = datetime.now()
        return self.data_dir / f"usage_{now.year}_{now.month:02d}.json"

    def _create_empty_usage(self) -> dict:
        now = datetime.now()
        return {
            "month": f"{now.year}-{now.month:02d}",
            "total_usd": 0.0,
            "entry_count": 0,
            "entries": [],
        }

    def _load_usage(self) -> dict:
        usage_path = self._get_usage_file_path()
        try:
            with open(usage_path, "r+", encoding="utf-8") as f:
                _lock_file(f, exclusive=False)
                try:
                    data = json.load(f)
                    # Recalculate totals from entries to prevent drift
                    data["total_usd"] = sum(
                        e.get("cost_usd", 0) for e in data.get("entries", [])
                    )
                    data["entry_count"] = len(data.get("entries", []))
                    return data
                finally:
                    _unlock_file(f)
        except FileNotFoundError:
            return self._create_empty_usage()
        except (json.JSONDecodeError, KeyError):
            backup_path = usage_path.with_suffix(".json.corrupted")
            try:
                usage_path.rename(backup_path)
            except OSError:
                pass
            return self._create_empty_usage()

    def _save_usage(self, data: dict) -> None:
        usage_path = self._get_usage_file_path()
        data["total_usd"] = sum(
            e.get("cost_usd", 0) for e in data.get("entries", [])
        )
        data["entry_count"] = len(data.get("entries", []))

        fd = os.open(str(usage_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with open(fd, "w", encoding="utf-8") as f:
            _lock_file(f, exclusive=True)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False)
            finally:
                _unlock_file(f)

    def _lookup_price(
        self,
        model: str,
        quality: str = "standard",
        size: str = "1024x1024",
        seconds: int | None = None,
        n: int = 1,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> float:
        if model.startswith("_comment"):
            raise ValueError(f"Invalid model name: {model}")

        if model not in self.pricing:
            available = [k for k in self.pricing if not k.startswith("_")]
            raise ValueError(
                f"Unknown model: {model}. Available: {available}"
            )

        entry = self.pricing[model]

        if entry["type"] == "per_token":
            if input_tokens is None or output_tokens is None:
                raise ValueError(
                    f"Model {model} requires 'input_tokens' and 'output_tokens'"
                )
            return (
                (input_tokens / 1000) * entry["input_per_1k"]
                + (output_tokens / 1000) * entry["output_per_1k"]
            )

        if entry["type"] == "per_second":
            if seconds is None:
                raise ValueError(f"Model {model} requires 'seconds'")
            return entry["rate"] * seconds

        # per_image
        variants = entry.get("variants", {})
        for key in [f"{quality}_{size}", "default", f"default_{size}"]:
            if key in variants:
                return variants[key] * n
        if "fallback" in entry:
            return entry["fallback"] * n

        raise ValueError(
            f"Cannot determine price for {model} "
            f"(quality={quality}, size={size})"
        )

    def estimate_cost(
        self,
        model: str,
        *,
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> float:
        """Estimate cost in USD without recording."""
        return self._lookup_price(
            model, quality, size, seconds, n, input_tokens, output_tokens
        )

    @staticmethod
    def _usage_by_scope(entries: list[dict]) -> dict:
        """Calculate usage totals grouped by provider and model."""
        by_provider: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for e in entries:
            cost = e.get("cost_usd", 0)
            p = e.get("provider", "")
            m = e.get("model", "")
            if p:
                by_provider[p] = by_provider.get(p, 0) + cost
            if m:
                by_model[m] = by_model.get(m, 0) + cost
        return {"by_provider": by_provider, "by_model": by_model}

    def check_budget(
        self,
        model: str,
        *,
        provider: str = "",
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> dict:
        """
        Check if budget allows this call. Does NOT record usage.

        Checks three layers (all must pass):
        1. Global monthly limit
        2. Provider limit (if configured)
        3. Model limit (if configured)

        Raises BudgetExceededError if any limit is exceeded.
        """
        with self._lock:
            estimated = self._lookup_price(
                model, quality, size, seconds, n, input_tokens, output_tokens
            )
            usage = self._load_usage()
            entries = usage.get("entries", [])

            # Layer 1: global limit
            used = usage["total_usd"]
            remaining = self.monthly_limit_usd - used
            if estimated > remaining:
                raise BudgetExceededError(
                    requested=estimated,
                    remaining=remaining,
                    limit=self.monthly_limit_usd,
                )

            scoped = self._usage_by_scope(entries)

            # Layer 2: provider limit
            if provider and provider in self.provider_limits:
                plimit = self.provider_limits[provider]
                pused = scoped["by_provider"].get(provider, 0)
                premaining = plimit - pused
                if estimated > premaining:
                    raise BudgetExceededError(
                        requested=estimated,
                        remaining=premaining,
                        limit=plimit,
                        scope="provider",
                        scope_name=provider,
                    )

            # Layer 3: model limit
            if model in self.model_limits:
                mlimit = self.model_limits[model]
                mused = scoped["by_model"].get(model, 0)
                mremaining = mlimit - mused
                if estimated > mremaining:
                    raise BudgetExceededError(
                        requested=estimated,
                        remaining=mremaining,
                        limit=mlimit,
                        scope="model",
                        scope_name=model,
                    )

            return {
                "allowed": True,
                "estimated_cost_usd": estimated,
                "remaining_usd": remaining,
                "monthly_limit_usd": self.monthly_limit_usd,
                "used_usd": used,
            }

    def hold_budget(
        self,
        model: str,
        *,
        provider: str = "",
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> str:
        """
        Check budget AND write a pessimistic hold entry.

        Like check_budget(), but also reserves the estimated cost in the
        usage log. If settle_hold() is never called, the hold amount
        stays — budget errs on the safe side.

        Returns:
            hold_id: Unique ID for this hold. Pass to settle_hold().
        """
        with self._lock:
            estimated = self._lookup_price(
                model, quality, size, seconds, n, input_tokens, output_tokens
            )
            usage = self._load_usage()
            entries = usage.get("entries", [])

            # Layer 1: global limit
            used = usage["total_usd"]
            remaining = self.monthly_limit_usd - used
            if estimated > remaining:
                raise BudgetExceededError(
                    requested=estimated,
                    remaining=remaining,
                    limit=self.monthly_limit_usd,
                )

            scoped = self._usage_by_scope(entries)

            # Layer 2: provider limit
            if provider and provider in self.provider_limits:
                plimit = self.provider_limits[provider]
                pused = scoped["by_provider"].get(provider, 0)
                premaining = plimit - pused
                if estimated > premaining:
                    raise BudgetExceededError(
                        requested=estimated,
                        remaining=premaining,
                        limit=plimit,
                        scope="provider",
                        scope_name=provider,
                    )

            # Layer 3: model limit
            if model in self.model_limits:
                mlimit = self.model_limits[model]
                mused = scoped["by_model"].get(model, 0)
                mremaining = mlimit - mused
                if estimated > mremaining:
                    raise BudgetExceededError(
                        requested=estimated,
                        remaining=mremaining,
                        limit=mlimit,
                        scope="model",
                        scope_name=model,
                    )

            # Write the hold entry
            hold_id = f"h_{uuid.uuid4().hex[:12]}"
            params: dict = {"n": n, "quality": quality, "size": size}
            if seconds is not None:
                params["seconds"] = seconds
            if input_tokens is not None:
                params["input_tokens"] = input_tokens
            if output_tokens is not None:
                params["max_output_tokens"] = output_tokens

            hold_entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "model": model,
                "provider": provider,
                "operation": "hold",
                "status": "hold",
                "hold_id": hold_id,
                "params": params,
                "cost_usd": estimated,
                "cumulative_usd": used + estimated,
                "caller": self.caller,
            }

            usage["entries"].append(hold_entry)
            self._save_usage(usage)

            return hold_id

    def settle_hold(
        self,
        hold_id: str,
        *,
        model: str = "",
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
        Settle a previously held budget reservation with actual usage.

        Finds the hold entry by hold_id and replaces its cost with
        the actual cost. If actual < estimated, budget is freed.
        """
        with self._lock:
            usage = self._load_usage()
            entries = usage.get("entries", [])

            # Find the hold entry
            hold_idx = None
            for i, e in enumerate(entries):
                if e.get("hold_id") == hold_id and e.get("status") == "hold":
                    hold_idx = i
                    break

            if hold_idx is None:
                # Hold not found — fall back to normal record
                return self._record_usage_locked(
                    usage, model=model, n=n, seconds=seconds,
                    quality=quality, size=size,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    provider=provider, operation=operation,
                )

            hold_entry = entries[hold_idx]
            actual_model = model or hold_entry.get("model", "")
            actual_provider = provider or hold_entry.get("provider", "")
            actual_cost = self._lookup_price(
                actual_model, quality, size, seconds, n, input_tokens, output_tokens
            )

            params: dict = {"n": n, "quality": quality, "size": size}
            if seconds is not None:
                params["seconds"] = seconds
            if input_tokens is not None:
                params["input_tokens"] = input_tokens
            if output_tokens is not None:
                params["output_tokens"] = output_tokens

            # Replace hold with settled entry
            entries[hold_idx] = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "model": actual_model,
                "provider": actual_provider,
                "operation": operation,
                "status": "settled",
                "hold_id": hold_id,
                "params": params,
                "cost_usd": actual_cost,
                "hold_cost_usd": hold_entry["cost_usd"],
                "caller": self.caller,
            }

            self._save_usage(usage)

            total_used = sum(e.get("cost_usd", 0) for e in entries)
            return {
                "cost_usd": actual_cost,
                "hold_cost_usd": hold_entry["cost_usd"],
                "saved_usd": hold_entry["cost_usd"] - actual_cost,
                "total_used_usd": total_used,
                "remaining_usd": self.monthly_limit_usd - total_used,
            }

    def _record_usage_locked(
        self,
        usage: dict,
        *,
        model: str,
        n: int = 1,
        seconds: int | None = None,
        quality: str = "standard",
        size: str = "1024x1024",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider: str = "",
        operation: str = "",
    ) -> dict:
        """Internal: record usage entry (caller must hold self._lock)."""
        cost = self._lookup_price(
            model, quality, size, seconds, n, input_tokens, output_tokens
        )

        params: dict = {"n": n, "quality": quality, "size": size}
        if seconds is not None:
            params["seconds"] = seconds
        if input_tokens is not None:
            params["input_tokens"] = input_tokens
        if output_tokens is not None:
            params["output_tokens"] = output_tokens

        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "provider": provider,
            "operation": operation,
            "params": params,
            "cost_usd": cost,
            "cumulative_usd": usage["total_usd"] + cost,
            "caller": self.caller,
        }

        usage["entries"].append(entry)
        self._save_usage(usage)

        total_used = usage["total_usd"] + cost
        return {
            "cost_usd": cost,
            "total_used_usd": total_used,
            "remaining_usd": self.monthly_limit_usd - total_used,
        }

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
        """Record a completed API call. Called AFTER successful response."""
        with self._lock:
            usage = self._load_usage()
            return self._record_usage_locked(
                usage, model=model, n=n, seconds=seconds,
                quality=quality, size=size,
                input_tokens=input_tokens, output_tokens=output_tokens,
                provider=provider, operation=operation,
            )

    def get_remaining_budget(self) -> dict:
        """Get current budget status with per-provider and per-model breakdowns."""
        with self._lock:
            usage = self._load_usage()
            entries = usage.get("entries", [])
            scoped = self._usage_by_scope(entries)

            # Build provider breakdown
            provider_status: dict[str, dict] = {}
            for p, used in scoped["by_provider"].items():
                limit = self.provider_limits.get(p)
                provider_status[p] = {
                    "used_usd": used,
                    "limit_usd": limit,
                    "remaining_usd": limit - used if limit is not None else None,
                }
            # Include providers with limits but no usage yet
            for p, limit in self.provider_limits.items():
                if p not in provider_status:
                    provider_status[p] = {
                        "used_usd": 0.0,
                        "limit_usd": limit,
                        "remaining_usd": limit,
                    }

            # Build model breakdown
            model_status: dict[str, dict] = {}
            for m, used in scoped["by_model"].items():
                limit = self.model_limits.get(m)
                model_status[m] = {
                    "used_usd": used,
                    "limit_usd": limit,
                    "remaining_usd": limit - used if limit is not None else None,
                }
            for m, limit in self.model_limits.items():
                if m not in model_status:
                    model_status[m] = {
                        "used_usd": 0.0,
                        "limit_usd": limit,
                        "remaining_usd": limit,
                    }

            return {
                "remaining_usd": self.monthly_limit_usd - usage["total_usd"],
                "used_usd": usage["total_usd"],
                "monthly_limit_usd": self.monthly_limit_usd,
                "provider_limits": self.provider_limits,
                "model_limits": self.model_limits,
                "month": usage["month"],
                "entry_count": usage["entry_count"],
                "by_provider": provider_status,
                "by_model": model_status,
            }

    def set_budget(
        self,
        *,
        global_limit: float | None = None,
        provider: str | None = None,
        provider_limit: float | None = None,
        model: str | None = None,
        model_limit: float | None = None,
    ) -> None:
        """Update budget limits and persist to config file."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        if global_limit is not None:
            config["monthly_limit_usd"] = global_limit
            self.monthly_limit_usd = global_limit

        if provider is not None:
            limits = config.setdefault("provider_limits", {})
            if provider_limit is not None and provider_limit > 0:
                limits[provider] = provider_limit
                self.provider_limits[provider] = provider_limit
            elif provider in limits:
                del limits[provider]
                self.provider_limits.pop(provider, None)

        if model is not None:
            limits = config.setdefault("model_limits", {})
            if model_limit is not None and model_limit > 0:
                limits[model] = model_limit
                self.model_limits[model] = model_limit
            elif model in limits:
                del limits[model]
                self.model_limits.pop(model, None)

        fd = os.open(str(self.config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

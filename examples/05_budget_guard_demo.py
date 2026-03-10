#!/usr/bin/env python3
"""
Example 5: Budget enforcement demo (no real API calls).

Demonstrates that get_key() physically blocks access when
the budget is exhausted. Run this without any API dependencies.

Setup:
    banto store openai    # store a dummy key for demo
"""

import json
import tempfile
import shutil
from pathlib import Path

from banto import SecureVault, BudgetExceededError, KeyNotFoundError
from banto.keychain import KeychainStore


def main():
    # --- Setup: temp config with $0.10 budget ---
    tmp_dir = Path(tempfile.mkdtemp(prefix="banto-demo-"))
    config = {
        "monthly_limit_usd": 0.10,
        "providers": {
            "openai": {"models": ["gpt-4o-mini"]},
        },
        "pricing": {
            "gpt-4o-mini": {
                "type": "per_token",
                "input_per_1k": 0.00015,
                "output_per_1k": 0.0006,
            },
        },
    }
    config_path = tmp_dir / "config.json"
    config_path.write_text(json.dumps(config))
    data_dir = tmp_dir / "data"

    # Store a demo key
    ks = KeychainStore(service_prefix="banto-demo")
    ks.store("openai", "demo-key-not-real-12345")

    vault = SecureVault(
        caller="demo",
        config_path=str(config_path),
        data_dir=str(data_dir),
        keychain_prefix="banto-demo",
    )

    print("=== Banto Budget Guard Demo ===\n")
    print(f"Budget: $0.10 (intentionally tiny)\n")

    # --- Simulate API calls until budget runs out ---
    call_count = 0
    while True:
        call_count += 1
        try:
            key = vault.get_key(
                model="gpt-4o-mini",
                input_tokens=1000,
                output_tokens=500,
            )
            # Simulate successful API call
            vault.record_usage(
                model="gpt-4o-mini",
                input_tokens=1000,
                output_tokens=500,
                provider="openai",
                operation="chat",
            )
            status = vault.get_budget_status()
            print(
                f"  Call {call_count}: OK  "
                f"(key={key[:10]}..., remaining=${status['remaining_usd']:.4f})"
            )

        except BudgetExceededError as e:
            print(
                f"  Call {call_count}: BLOCKED  "
                f"(remaining=${e.remaining:.4f}, requested=${e.requested:.4f})"
            )
            print(f"\n  The key was never returned. The agent cannot call the API.")
            break

    # --- Final status ---
    status = vault.get_budget_status()
    print(f"\n=== Final Status ===")
    print(f"  Calls made:  {call_count - 1}")
    print(f"  Calls blocked: 1")
    print(f"  Used:  ${status['used_usd']:.4f}")
    print(f"  Limit: ${status['monthly_limit_usd']:.2f}")

    # --- Cleanup ---
    ks.delete("openai")
    shutil.rmtree(tmp_dir)
    print(f"\n  (demo keys and data cleaned up)")


if __name__ == "__main__":
    main()

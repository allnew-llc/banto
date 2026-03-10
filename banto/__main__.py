"""
CLI entry point: python -m banto <command>

Commands:
    status              Show budget status
    store <provider>    Store an API key in Keychain
    list                List stored provider keys
    check <model> ...   Dry-run budget check for a model
    init                Copy default config to ~/.config/banto/
"""

import getpass
import shutil
import sys
from pathlib import Path

from .guard import CostGuard, CONFIG_DIR
from .keychain import KeychainStore, _validate_provider
from .profiles import ProfileManager
from .vault import SecureVault


_PROFILE_DESCRIPTIONS = {
    "quality": "Use premium models for best results",
    "balanced": "Mix of quality and cost efficiency",
    "budget": "Prioritize cost savings",
}


def cmd_status(args: list[str]) -> None:
    guard = CostGuard(caller="cli")
    s = guard.get_remaining_budget()
    print(f"Month:     {s['month']}")
    print(f"Used:      ${s['used_usd']:.2f}")
    print(f"Remaining: ${s['remaining_usd']:.2f}")
    print(f"Limit:     ${s['monthly_limit_usd']:.2f}")
    print(f"Entries:   {s['entry_count']}")

    # Voided timeout holds
    voided_count = s.get("voided_timeout_count", 0)
    if voided_count > 0:
        voided_usd = s.get("voided_timeout_usd", 0)
        print(f"Stale holds voided: {voided_count} (${voided_usd:.2f} released)")

    # Recommended profile
    profile = guard.recommend_profile()
    limit = s["monthly_limit_usd"]
    if limit > 0:
        remaining_pct = int((limit - s["used_usd"]) / limit * 100)
        print(f"\nRecommended profile: {profile} "
              f"({remaining_pct}% budget remaining)")
    else:
        print(f"\nRecommended profile: {profile} (no limit set)")
    print(f"  -> {_PROFILE_DESCRIPTIONS[profile]}")

    # Provider breakdown
    by_provider = s.get("by_provider", {})
    if by_provider:
        print(f"\nBy provider:")
        for p, info in sorted(by_provider.items()):
            used = info["used_usd"]
            limit = info["limit_usd"]
            if limit is not None:
                print(f"  {p:20s}  ${used:.2f} / ${limit:.2f}  (${info['remaining_usd']:.2f} remaining)")
            else:
                print(f"  {p:20s}  ${used:.2f}")

    # Model breakdown
    by_model = s.get("by_model", {})
    if by_model:
        print(f"\nBy model:")
        for m, info in sorted(by_model.items()):
            used = info["used_usd"]
            limit = info["limit_usd"]
            if limit is not None:
                print(f"  {m:35s}  ${used:.2f} / ${limit:.2f}  (${info['remaining_usd']:.2f} remaining)")
            else:
                print(f"  {m:35s}  ${used:.2f}")


def cmd_store(args: list[str]) -> None:
    if not args:
        print("Usage: banto store <provider>")
        print("Example: banto store openai")
        sys.exit(1)

    try:
        provider = _validate_provider(args[0])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    keychain = KeychainStore()

    if keychain.exists(provider):
        overwrite = input(
            f"Key for '{provider}' already exists. Overwrite? (y/N): "
        )
        if overwrite.strip().lower() != "y":
            print("Cancelled.")
            return

    api_key = getpass.getpass(f"Enter API key for '{provider}': ")
    if not api_key:
        print("Empty input. Cancelled.")
        return

    if keychain.store(provider, api_key):
        print(f"Stored '{provider}' in Keychain.")
    else:
        print(f"Failed to store '{provider}'.", file=sys.stderr)
        sys.exit(1)


def cmd_delete(args: list[str]) -> None:
    if not args:
        print("Usage: banto delete <provider>")
        sys.exit(1)

    try:
        provider = _validate_provider(args[0])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    keychain = KeychainStore()

    if not keychain.exists(provider):
        print(f"No key found for '{provider}'.")
        return

    confirm = input(f"Delete key for '{provider}'? (y/N): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    if keychain.delete(provider):
        print(f"Deleted '{provider}' from Keychain.")
    else:
        print(f"Failed to delete '{provider}'.", file=sys.stderr)
        sys.exit(1)


def cmd_list(args: list[str]) -> None:
    vault = SecureVault(caller="cli")
    providers = vault.list_providers()
    budget = vault.get_budget_status()

    print(f"\nBudget: ${budget['used_usd']:.2f} / ${budget['monthly_limit_usd']:.2f} "
          f"(${budget['remaining_usd']:.2f} remaining)\n")

    if providers:
        print("Stored keys:")
        for p in providers:
            print(f"  + {p}")
    else:
        print("No keys stored. Use 'banto store <provider>' to add one.")
    print()


def cmd_check(args: list[str]) -> None:
    if not args:
        print("Usage: banto check <model> [--tokens <in> <out>] [--n <count>]")
        sys.exit(1)

    model = args[0]
    kwargs: dict = {}

    i = 1
    while i < len(args):
        if args[i] == "--tokens" and i + 2 < len(args):
            kwargs["input_tokens"] = int(args[i + 1])
            kwargs["output_tokens"] = int(args[i + 2])
            i += 3
        elif args[i] == "--n" and i + 1 < len(args):
            kwargs["n"] = int(args[i + 1])
            i += 2
        elif args[i] == "--seconds" and i + 1 < len(args):
            kwargs["seconds"] = int(args[i + 1])
            i += 2
        elif args[i] == "--quality" and i + 1 < len(args):
            kwargs["quality"] = args[i + 1]
            i += 2
        elif args[i] == "--size" and i + 1 < len(args):
            kwargs["size"] = args[i + 1]
            i += 2
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)

    guard = CostGuard(caller="cli")
    cost = guard.estimate_cost(model=model, **kwargs)
    budget = guard.get_remaining_budget()

    print(f"Model:     {model}")
    print(f"Estimated: ${cost:.4f}")
    print(f"Remaining: ${budget['remaining_usd']:.2f}")
    if cost <= budget["remaining_usd"]:
        print("Result:    ALLOWED")
    else:
        print("Result:    BLOCKED (over budget)")


def cmd_profile(args: list[str]) -> None:
    """Show or set the active model profile."""
    vault = SecureVault(caller="cli")
    profiles = vault.get_profiles()

    if not args:
        # Show all profiles
        print("Model profiles:\n")
        for name, info in profiles.items():
            marker = " *" if info["active"] else "  "
            desc = _PROFILE_DESCRIPTIONS.get(name, "")
            print(f" {marker} {name:12s}  {desc}")
            for role, model in info["models"].items():
                print(f"      {role:8s} -> {model}")
            print()
        return

    profile_name = args[0]
    try:
        vault.set_profile(profile_name)
        print(f"Active profile set to '{profile_name}'.")
        desc = _PROFILE_DESCRIPTIONS.get(profile_name, "")
        if desc:
            print(f"  -> {desc}")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_budget(args: list[str]) -> None:
    guard = CostGuard(caller="cli")

    if not args:
        # Show current budget settings
        s = guard.get_remaining_budget()
        print(f"Global:    ${s['monthly_limit_usd']:.2f}")
        if s["provider_limits"]:
            print(f"\nProvider limits:")
            for p, limit in sorted(s["provider_limits"].items()):
                print(f"  {p:20s}  ${limit:.2f}")
        if s["model_limits"]:
            print(f"\nModel limits:")
            for m, limit in sorted(s["model_limits"].items()):
                print(f"  {m:35s}  ${limit:.2f}")
        if not s["provider_limits"] and not s["model_limits"]:
            print("\nNo provider or model limits set.")
            print("Use 'banto budget --provider openai 30' or 'banto budget --model dall-e-3 10'")
        return

    # Parse: banto budget <amount>
    #        banto budget --provider <name> <amount>
    #        banto budget --model <name> <amount>
    #        banto budget --provider <name> --remove
    #        banto budget --model <name> --remove
    i = 0
    global_limit: float | None = None
    provider: str | None = None
    provider_limit: float | None = None
    model: str | None = None
    model_limit: float | None = None
    remove = False

    while i < len(args):
        if args[i] == "--provider" and i + 1 < len(args):
            provider = args[i + 1]
            i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        elif args[i] == "--remove":
            remove = True
            i += 1
        else:
            try:
                amount = float(args[i])
                if provider is not None:
                    provider_limit = amount
                elif model is not None:
                    model_limit = amount
                else:
                    global_limit = amount
                i += 1
            except ValueError:
                print(f"Invalid argument: {args[i]}", file=sys.stderr)
                sys.exit(1)

    if remove:
        if provider:
            provider_limit = 0.0  # 0 = remove
        if model:
            model_limit = 0.0

    if global_limit is None and provider is None and model is None:
        print("Usage: banto budget <amount>", file=sys.stderr)
        print("       banto budget --provider <name> <amount>", file=sys.stderr)
        print("       banto budget --model <name> <amount>", file=sys.stderr)
        print("       banto budget --provider <name> --remove", file=sys.stderr)
        sys.exit(1)

    guard.set_budget(
        global_limit=global_limit,
        provider=provider,
        provider_limit=provider_limit,
        model=model,
        model_limit=model_limit,
    )

    if global_limit is not None:
        print(f"Global limit set to ${global_limit:.2f}")
    if provider and remove:
        print(f"Provider limit for '{provider}' removed.")
    elif provider and provider_limit:
        print(f"Provider limit for '{provider}' set to ${provider_limit:.2f}")
    if model and remove:
        print(f"Model limit for '{model}' removed.")
    elif model and model_limit:
        print(f"Model limit for '{model}' set to ${model_limit:.2f}")


def cmd_init(args: list[str]) -> None:
    config_dest = CONFIG_DIR / "config.json"
    pricing_dest = CONFIG_DIR / "pricing.json"

    if config_dest.exists():
        print(f"Config already exists: {config_dest}")
        overwrite = input("Overwrite with default? (y/N): ")
        if overwrite.strip().lower() != "y":
            print("Cancelled.")
            return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    bundled = Path(__file__).parent
    shutil.copy2(bundled / "config.json", config_dest)
    config_dest.chmod(0o640)

    shutil.copy2(bundled / "pricing.json", pricing_dest)
    pricing_dest.chmod(0o640)

    print(f"Config written to:  {config_dest}")
    print(f"Pricing written to: {pricing_dest}")
    print()
    print("Next steps:")
    print("  1. Set your budget:   banto budget <amount>")
    print("  2. Review pricing:    edit pricing.json to match current provider rates")
    print("  3. Store API keys:    banto store <provider>")


COMMANDS = {
    "status": cmd_status,
    "budget": cmd_budget,
    "profile": cmd_profile,
    "store": cmd_store,
    "delete": cmd_delete,
    "list": cmd_list,
    "check": cmd_check,
    "init": cmd_init,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("banto: Budget-gated API key vault for LLM applications\n")
        print("Usage: banto <command> [args]\n")
        print("Commands:")
        print("  status              Show budget status (with per-provider/model breakdown)")
        print("  budget [args]       View or set budget limits")
        print("  profile [name]      Show or set the active model profile")
        print("  store <provider>    Store an API key in Keychain")
        print("  delete <provider>   Delete an API key from Keychain")
        print("  list                List stored keys and budget")
        print("  check <model> ...   Dry-run budget check")
        print("  init                Copy default config to ~/.config/banto/")
        print()
        print("Budget examples:")
        print("  banto budget                          Show all limits")
        print("  banto budget 100                      Set global limit to $100")
        print("  banto budget --provider openai 30     Set OpenAI limit to $30")
        print("  banto budget --model dall-e-3 10      Set DALL-E 3 limit to $10")
        print("  banto budget --provider openai --remove  Remove provider limit")
        print()
        print("Profile examples:")
        print("  banto profile                         Show all profiles")
        print("  banto profile balanced                Set active profile")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(f"Available: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()

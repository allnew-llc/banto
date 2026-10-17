# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
CLI entry point: python -m banto <command>

Commands:
    status              Show budget status
    store <provider>    Store an API key in Keychain
    register [provider] Open browser popup to store an API key
    register-asc        Open App Store Connect credential registration popup
    list                List stored provider keys
    check <model> ...   Dry-run budget check for a model
    init                Copy default config to ~/.config/banto/
    version             Show the installed version (also --version, -V)
"""

import getpass
import json
import shutil
import sys
from pathlib import Path

from .guard import CostGuard, CONFIG_DIR


def _is_json(args: list[str]) -> bool:
    """Check if --json flag is present."""
    return "--json" in args


def _json_out(data: dict | list) -> None:
    """Print JSON to stdout and exit."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
from .keychain import KeychainStore, _validate_provider
from .sync.config import SyncConfig
from .vault import SecureVault


def _validate_cli_bounds(
    *,
    tokens: int | None = None,
    seconds: int | None = None,
    n: int | None = None,
    budget: float | None = None,
    label: str = "",
) -> None:
    """Validate CLI numeric arguments are within reasonable bounds."""
    if tokens is not None and not (0 <= tokens <= 100_000_000):
        print(
            f"Error: {label}tokens must be between 0 and 100,000,000, got {tokens}",
            file=sys.stderr,
        )
        sys.exit(1)
    if seconds is not None and not (0 <= seconds <= 86400):
        print(
            f"Error: seconds must be between 0 and 86,400, got {seconds}",
            file=sys.stderr,
        )
        sys.exit(1)
    if n is not None and not (1 <= n <= 10_000):
        print(
            f"Error: n must be between 1 and 10,000, got {n}",
            file=sys.stderr,
        )
        sys.exit(1)
    if budget is not None and not (0 <= budget <= 1_000_000):
        print(
            f"Error: budget amount must be between 0 and 1,000,000, got {budget}",
            file=sys.stderr,
        )
        sys.exit(1)


_PROFILE_DESCRIPTIONS = {
    "quality": "Use premium models for best results",
    "balanced": "Mix of quality and cost efficiency",
    "budget": "Prioritize cost savings",
}


def cmd_status(args: list[str]) -> None:
    vault = SecureVault(caller="cli")
    if not vault.budget_enabled:
        if _is_json(args):
            _json_out({"budget_enabled": False})
            return
        print("Budget: disabled (no monthly_limit_usd in config)")
        print("\nEnable with: banto budget 100")
        return
    guard = vault._guard
    assert guard is not None
    if _is_json(args):
        s = guard.get_remaining_budget()
        s["recommended_profile"] = guard.recommend_profile()
        _json_out(s)
        return
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
        print("\nBy provider:")
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
        print("\nBy model:")
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
    config = SyncConfig.load()
    keychain = KeychainStore(service_prefix=config.keychain_service)

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
    config = SyncConfig.load()
    keychain = KeychainStore(service_prefix=config.keychain_service)

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
    # Same Keychain namespace as `banto store` / `banto register`.
    sync_config = SyncConfig.load()
    vault = SecureVault(caller="cli", keychain_prefix=sync_config.keychain_service)
    accounts = [entry.account for entry in sync_config.secrets.values() if entry.account]
    providers = vault.list_providers(extra=accounts)
    budget = vault.get_budget_status()

    if _is_json(args):
        _json_out({"providers": providers, "budget": budget})
        return

    if budget.get("budget_enabled", True):
        print(f"\nBudget: ${budget['used_usd']:.2f} / ${budget['monthly_limit_usd']:.2f} "
              f"(${budget['remaining_usd']:.2f} remaining)\n")
    else:
        print("\nBudget: disabled\n")

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
            in_tok = int(args[i + 1])
            out_tok = int(args[i + 2])
            _validate_cli_bounds(tokens=in_tok, label="input_")
            _validate_cli_bounds(tokens=out_tok, label="output_")
            kwargs["input_tokens"] = in_tok
            kwargs["output_tokens"] = out_tok
            i += 3
        elif args[i] == "--n" and i + 1 < len(args):
            n_val = int(args[i + 1])
            _validate_cli_bounds(n=n_val)
            kwargs["n"] = n_val
            i += 2
        elif args[i] == "--seconds" and i + 1 < len(args):
            sec_val = int(args[i + 1])
            _validate_cli_bounds(seconds=sec_val)
            kwargs["seconds"] = sec_val
            i += 2
        elif args[i] == "--quality" and i + 1 < len(args):
            kwargs["quality"] = args[i + 1]
            i += 2
        elif args[i] == "--size" and i + 1 < len(args):
            kwargs["size"] = args[i + 1]
            i += 2
        elif args[i] == "--json":
            i += 1
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)

    from .guard import BudgetExceededError
    vault = SecureVault(caller="cli")
    guard = vault._guard or CostGuard(caller="cli")
    try:
        cost = guard.estimate_cost(model=model, **kwargs)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not vault.budget_enabled:
        if _is_json(args):
            _json_out({"model": model, "estimated_usd": cost, "budget_enabled": False,
                       "allowed": True})
            return
        print(f"Model:     {model}")
        print(f"Estimated: ${cost:.4f}")
        print("Result:    ALLOWED (budget disabled — enable with: banto budget <amount>)")
        return

    # Same three-layer check (global, provider, model) as hold_budget().
    provider = kwargs.pop("provider", "") or vault._provider_map.get(model, "")
    blocked = None
    try:
        guard.check_budget(model, provider=provider, **kwargs)
    except BudgetExceededError as e:
        blocked = e
    remaining = guard.get_remaining_budget()["remaining_usd"]

    if _is_json(args):
        out = {"model": model, "estimated_usd": cost, "remaining_usd": remaining,
               "budget_enabled": True, "allowed": blocked is None}
        if blocked is not None:
            out.update({"blocked_scope": blocked.scope, "blocked_scope_name": blocked.scope_name,
                        "scope_remaining_usd": blocked.remaining})
        _json_out(out)
        return

    print(f"Model:     {model}")
    print(f"Estimated: ${cost:.4f}")
    print(f"Remaining: ${remaining:.2f}")
    if blocked is None:
        print("Result:    ALLOWED")
    elif blocked.scope == "global":
        print("Result:    BLOCKED (over monthly budget)")
    else:
        print(f"Result:    BLOCKED (over {blocked.scope} '{blocked.scope_name}' limit, "
              f"${blocked.remaining:.2f} remaining)")


def cmd_profile(args: list[str]) -> None:
    """Show or set the active model profile."""
    vault = SecureVault(caller="cli")
    profiles = vault.get_profiles()

    if _is_json(args):
        _json_out(profiles)
        return

    if not args or (len(args) == 1 and args[0] == "--json"):
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
        vault.set_profile(profile_name, persist=True)
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
            print("\nProvider limits:")
            for p, limit in sorted(s["provider_limits"].items()):
                print(f"  {p:20s}  ${limit:.2f}")
        if s["model_limits"]:
            print("\nModel limits:")
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
                _validate_cli_bounds(budget=amount)
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


def cmd_register(args: list[str]) -> None:
    from .register_popup import serve_register_popup

    provider_hint = args[0] if args else None
    print("Opening browser for API key registration...")
    url = serve_register_popup(provider_hint=provider_hint, blocking=True)
    print(f"Server was at: {url}")
    print("Done.")


def cmd_register_asc(args: list[str]) -> None:
    from .register_popup import serve_asc_register_popup

    print("Opening browser for App Store Connect credential registration...")
    url = serve_asc_register_popup(blocking=True)
    print(f"Server was at: {url}")
    print("Done.")


def cmd_register_x_oauth(args: list[str]) -> None:
    """There is no X OAuth popup; point to the supported PKCE enrolment."""
    print("Error: 'banto register-x-oauth' is not available.\n"
          "Enroll an X OAuth 2.0 user token with PKCE instead:\n"
          "  banto sync x-oauth-authorize <name> ...   (see: banto sync x-oauth-authorize --help)",
          file=sys.stderr)
    sys.exit(2)


def cmd_sync(args: list[str]) -> None:
    from .sync.cli import cmd_sync_dispatch
    cmd_sync_dispatch(args)


def cmd_lease(args: list[str]) -> None:
    from .lease_cli import cmd_lease_dispatch
    cmd_lease_dispatch(args)


def cmd_sealed(args: list[str]) -> None:
    from .sealed_cli import cmd_sealed_dispatch
    cmd_sealed_dispatch(args)


def _print_version() -> None:
    from . import __version__
    print(f"banto {__version__}")


def cmd_version(args: list[str]) -> None:
    _print_version()


def cmd_chatgpt(args: list[str]) -> None:
    if not args or args[0] in ("-h", "--help"):
        print("banto chatgpt: Connect banto to ChatGPT\n")
        print("Usage: banto chatgpt connect [--ngrok|--cloudflared] [--port N]")
        return
    if args[0] == "connect":
        from .chatgpt import connect
        connect(args[1:])
    else:
        print(f"Unknown chatgpt command: {args[0]}")


COMMANDS = {
    "status": cmd_status,
    "budget": cmd_budget,
    "profile": cmd_profile,
    "store": cmd_store,
    "register": cmd_register,
    "register-asc": cmd_register_asc,
    "asc-register": cmd_register_asc,
    "register-x-oauth": cmd_register_x_oauth,
    "delete": cmd_delete,
    "list": cmd_list,
    "check": cmd_check,
    "init": cmd_init,
    "sync": cmd_sync,
    "lease": cmd_lease,
    "sealed": cmd_sealed,
    "chatgpt": cmd_chatgpt,
    "version": cmd_version,
}

VERSION_FLAGS = ("--version", "-V", "version")


def main() -> None:
    # Handled before broker routing so it works in broker mode too and never
    # touches Keychain, the broker socket, or config files.
    if len(sys.argv) > 1 and sys.argv[1] in VERSION_FLAGS:
        _print_version()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "broker":
        from .broker import main as broker_main
        sys.argv.pop(1)
        broker_main()
        return
    if len(sys.argv) > 1:
        from .broker_cli import route_if_enabled
        if route_if_enabled(sys.argv[1:]):
            return
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("banto: Budget-gated API key vault for LLM applications\n")
        print("Usage: banto <command> [args]")
        print("       banto --version | -V\n")
        print("Commands:")
        print("  broker <cmd>        Common Keychain process: install, health, enable, disable, call, audit")
        print("  status              Show budget status (with per-provider/model breakdown)")
        print("  budget [args]       View or set budget limits")
        print("  profile [name]      Show or set the active model profile")
        print("  store <provider>    Store an API key in Keychain (terminal)")
        print("  register [provider] Open browser popup to store an API key")
        print("  register-asc        Open App Store Connect (.p8) credential registration popup")
        print("  delete <provider>   Delete an API key from Keychain")
        print("  list                List stored keys and budget")
        print("  check <model> ...   Dry-run budget check")
        print("  init                Copy default config to ~/.config/banto/")
        print("  sync <cmd>          Multi-platform secret sync (33 platforms)")
        print("  lease <cmd>         Dynamic secrets with TTL and auto-revoke")
        print("  sealed <cmd>        Secure Enclave sealed signing (non-extractable keys)")
        print("  chatgpt connect     Connect banto to ChatGPT (starts tunnel)")
        print("  version             Show the installed version (also --version, -V)")
        print()
        print("Broker mode (after `banto broker enable`): store, delete, lease and most sync")
        print("subcommands are disabled; register, sync status/audit/validate/push are routed")
        print("through the broker. See docs/local-broker.md.")
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

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""CLI subcommands for banto sync — multi-platform secret sync.

Accessed via: banto sync <subcommand>
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

from ..keychain import KeychainStore
from .config import SyncConfig, SecretEntry, Target, DEFAULT_CONFIG_PATH
from .history import HistoryStore
from .sync import SyncReport, check_status, sync_all, sync_secret, remove_secret


def _print_report(report: SyncReport) -> None:
    for r in report.results:
        mark = "OK" if r.success else "FAIL"
        msg = f"  [{mark}] {r.secret_name} -> {r.target_label}"
        if r.message and not r.success:
            msg += f"  ({r.message})"
        print(msg)
    print(f"\n  {report.ok_count} succeeded, {report.fail_count} failed")


def _load_config(args: list[str]) -> tuple[SyncConfig, Path]:
    """Load sync config, respecting --config flag."""
    config_path = DEFAULT_CONFIG_PATH
    for i, a in enumerate(args):
        if a == "--config" and i + 1 < len(args):
            config_path = Path(args[i + 1])
            break
    return SyncConfig.load(config_path), config_path


def cmd_sync_status(args: list[str]) -> None:
    config, _ = _load_config(args)
    if not config.secrets:
        print("BANTO SYNC — No secrets configured.")
        return

    entries = check_status(config)
    all_targets: list[str] = []
    for entry in entries:
        for label in entry.target_status:
            if label not in all_targets:
                all_targets.append(label)

    short_labels = []
    for t in all_targets:
        if ":" in t:
            parts = t.split(":", 1)
            platform = parts[0].replace("cloudflare-pages", "CF").replace("vercel", "Vercel")
            short_labels.append(f"{platform}:{parts[1][:12]}")
        else:
            name = Path(t).name if "/" in t else t
            short_labels.append(name[:16])

    print(f"\nBANTO SYNC — Secret Registry\n")
    print(f"  Keychain service: {config.keychain_service} ({len(config.secrets)} keys)\n")

    col_w = max(14, *(len(s) for s in short_labels)) if short_labels else 14
    header = f"  | {'Secret':<20} | {'Keychain':^8} |"
    for sl in short_labels:
        header += f" {sl:^{col_w}} |"
    print(header)
    print(f"  |{'-' * 22}|{'-' * 10}|" + "|".join(f"{'-' * (col_w + 2)}" for _ in short_labels) + "|")

    missing: list[str] = []
    for entry in entries:
        kc = "\u2713" if entry.keychain_exists else "\u2717"
        row = f"  | {entry.env_name:<20} | {kc:^8} |"
        for i, label in enumerate(all_targets):
            status = entry.target_status.get(label)
            if status is None:
                sym = "\u2014"
            elif status:
                sym = "\u2713"
            else:
                sym = "\u2717"
                missing.append(f"{entry.env_name} -> {short_labels[i]}")
            row += f" {sym:^{col_w}} |"
        print(row)

    if missing:
        print(f"\n  Warning: {len(missing)} secret(s) missing:")
        for m in missing:
            print(f"    {m}")
        print("  Run: banto sync push")
    else:
        print("\n  All secrets in sync.")


def cmd_sync_push(args: list[str]) -> None:
    """Push secrets from Keychain to all targets."""
    config, _ = _load_config(args)
    # Check if a specific name was given
    name = None
    for a in args:
        if not a.startswith("--"):
            name = a
            break

    if name:
        report = sync_secret(config, name)
    else:
        report = sync_all(config)

    _print_report(report)
    if not report.all_ok:
        sys.exit(1)


def cmd_sync_add(args: list[str]) -> None:
    """Add a secret to sync config."""
    config, config_path = _load_config(args)

    # Parse: banto sync add <name> --env <ENV_VAR> [--target platform:project ...]
    name = None
    env_name = None
    description = ""
    targets: list[str] = []

    i = 0
    while i < len(args):
        if args[i] == "--env" and i + 1 < len(args):
            env_name = args[i + 1]
            i += 2
        elif args[i] == "--description" and i + 1 < len(args):
            description = args[i + 1]
            i += 2
        elif args[i] == "--target" and i + 1 < len(args):
            targets.append(args[i + 1])
            i += 2
        elif args[i] == "--config":
            i += 2  # skip
        elif not args[i].startswith("--") and name is None:
            name = args[i]
            i += 1
        else:
            i += 1

    if not name or not env_name:
        print("Usage: banto sync add <name> --env <ENV_VAR> [--target platform:project]")
        sys.exit(1)

    if config.get_secret(name):
        print(f"Error: Secret '{name}' already exists.")
        sys.exit(1)

    value = getpass.getpass(f"Enter value for {name}: ")
    if not value:
        print("Empty value. Cancelled.")
        sys.exit(1)

    # Store in Keychain
    kc = KeychainStore(service_prefix=config.keychain_service)
    if not kc.store(name, value):
        print("Error: Failed to store in Keychain.")
        sys.exit(1)

    # Parse targets
    parsed_targets: list[Target] = []
    for t_str in targets:
        if ":" not in t_str:
            print(f"Error: Target must be platform:project — got '{t_str}'")
            sys.exit(1)
        platform, project = t_str.split(":", 1)
        if platform == "local":
            parsed_targets.append(Target(platform="local", file=project))
        else:
            parsed_targets.append(Target(platform=platform, project=project))

    entry = SecretEntry(
        name=name, account=name, env_name=env_name,
        description=description, targets=parsed_targets,
    )
    config.add_secret(entry)
    config.save(config_path)

    # Record history
    history = HistoryStore()
    history.record(name, value, config.keychain_service)

    print(f"Added '{name}' ({env_name}) with {len(parsed_targets)} target(s).")

    if parsed_targets:
        print("Syncing to targets...")
        report = sync_secret(config, name)
        _print_report(report)


def cmd_sync_audit(args: list[str]) -> None:
    """Check drift across all targets."""
    config, _ = _load_config(args)
    entries = check_status(config)
    issues: list[str] = []

    for entry in entries:
        if not entry.keychain_exists:
            issues.append(f"  MISSING in Keychain: {entry.env_name}")
        for label, status in entry.target_status.items():
            if status is False:
                issues.append(f"  MISSING {entry.env_name} -> {label}")

    if issues:
        print(f"BANTO SYNC AUDIT — {len(issues)} issue(s) found:\n")
        for issue in issues:
            print(issue)
        sys.exit(1)
    else:
        print("BANTO SYNC AUDIT — All secrets in sync.")


def cmd_sync_history(args: list[str]) -> None:
    """Show version history for a secret."""
    if not args or args[0].startswith("--"):
        print("Usage: banto sync history <name>")
        sys.exit(1)

    name = args[0]
    history = HistoryStore()
    versions = history.list_versions(name)
    if not versions:
        print(f"No history for '{name}'.")
        return

    print(f"\nVersion history: {name}\n")
    for v in reversed(versions):
        current = " (current)" if v.version == versions[-1].version else ""
        print(f"  v{v.version}  {v.timestamp}  fingerprint={v.fingerprint}{current}")
    print(f"\n  {len(versions)} version(s)")


def cmd_sync_export(args: list[str]) -> None:
    """Export secrets in various formats."""
    config, _ = _load_config(args)
    kc = KeychainStore(service_prefix=config.keychain_service)

    fmt = "env"
    env_name = None
    for i, a in enumerate(args):
        if a == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
        elif a == "--env" and i + 1 < len(args):
            env_name = args[i + 1]

    if env_name:
        resolved = config.resolve_environment(env_name)
    else:
        resolved = dict(config.secrets)

    if not resolved:
        print("No secrets to export.")
        return

    secrets: dict[str, str] = {}
    for _name, entry in resolved.items():
        val = kc.get(entry.account)
        secrets[entry.env_name] = val or ""

    if fmt == "env":
        for k, v in secrets.items():
            if "\n" in v or "#" in v or " " in v:
                v = '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
            print(f"{k}={v}")
    elif fmt == "json":
        print(json.dumps(secrets, indent=2, ensure_ascii=False))
    elif fmt == "docker":
        for k, v in secrets.items():
            print(f"{k}={v}")
    else:
        print(f"Error: Unknown format '{fmt}'. Supported: env, json, docker")
        sys.exit(1)


def cmd_sync_init(args: list[str]) -> None:
    """Create a default sync.json config."""
    config_path = DEFAULT_CONFIG_PATH
    if config_path.exists():
        print(f"Config already exists: {config_path}")
        overwrite = input("Overwrite with default? (y/N): ")
        if overwrite.strip().lower() != "y":
            print("Cancelled.")
            return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    default = {
        "version": 1,
        "keychain_service": "banto-sync",
        "secrets": {},
    }
    config_path.write_text(
        json.dumps(default, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Sync config created: {config_path}")
    print("\nNext steps:")
    print("  banto sync add <name> --env <ENV_VAR> --target platform:project")
    print("  banto sync push")


def cmd_sync_ui(args: list[str]) -> None:
    """Launch local web UI."""
    from .web import serve
    config, _ = _load_config(args)
    port = 8384
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
    serve(config, port=port)


SYNC_COMMANDS = {
    "status": cmd_sync_status,
    "push": cmd_sync_push,
    "add": cmd_sync_add,
    "audit": cmd_sync_audit,
    "history": cmd_sync_history,
    "export": cmd_sync_export,
    "init": cmd_sync_init,
    "ui": cmd_sync_ui,
}


def cmd_sync_dispatch(args: list[str]) -> None:
    """Dispatch banto sync <subcommand>."""
    if not args or args[0] in ("-h", "--help"):
        print("banto sync: Multi-platform secret sync\n")
        print("Usage: banto sync <command> [args]\n")
        print("Commands:")
        print("  init                Create default sync config")
        print("  status              Show sync status matrix")
        print("  push [name]         Sync secrets to targets")
        print("  add <name> ...      Add a new secret")
        print("  audit               Check drift across targets")
        print("  history <name>      Show version history")
        print("  export [--format]   Export secrets (env/json/docker)")
        print("  ui [--port N]       Launch local web UI")
        sys.exit(0)

    sub = args[0]
    if sub not in SYNC_COMMANDS:
        print(f"Unknown sync command: {sub}", file=sys.stderr)
        print(f"Available: {', '.join(SYNC_COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    SYNC_COMMANDS[sub](args[1:])

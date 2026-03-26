# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""CLI subcommands for banto sync — multi-platform secret sync.

Accessed via: banto sync <subcommand>
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..keychain import KeychainStore
from .config import SyncConfig, SecretEntry, Target, DEFAULT_CONFIG_PATH
from .history import HistoryStore


def _is_json(args: list[str]) -> bool:
    return "--json" in args


def _json_out(data: dict | list) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
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
        if _is_json(args):
            _json_out({"secrets": [], "count": 0})
            return
        print("BANTO SYNC — No secrets configured.")
        return

    if _is_json(args):
        entries = check_status(config)
        _json_out([
            {"name": e.secret_name, "env_name": e.env_name,
             "keychain": e.keychain_exists,
             "targets": {k: v for k, v in e.target_status.items()}}
            for e in entries
        ])
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

    do_validate = "--validate" in args
    name = None
    for a in args:
        if not a.startswith("--"):
            name = a
            break

    # Pre-push validation
    if do_validate:
        from .validate import validate_key
        kc = KeychainStore(service_prefix=config.keychain_service)
        secrets_to_check = {name: config.get_secret(name)} if name else config.secrets
        invalid = []
        for sname, entry in secrets_to_check.items():
            if entry is None:
                continue
            value = kc.get(entry.account)
            if value is None:
                continue
            result = validate_key(sname, value)
            if not result.valid:
                invalid.append(f"  {sname}: {result.message}")
        if invalid:
            print("Pre-push validation FAILED:\n")
            for line in invalid:
                print(line)
            print("\nFix invalid keys before pushing. Use --no-validate to skip.")
            sys.exit(1)
        print("Pre-push validation passed.\n")

    if name:
        report = sync_secret(config, name)
    else:
        report = sync_all(config)

    if _is_json(args):
        _json_out({"ok": report.all_ok, "ok_count": report.ok_count,
                    "fail_count": report.fail_count,
                    "results": [{"name": r.secret_name, "target": r.target_label,
                                 "success": r.success, "message": r.message}
                                for r in report.results]})
        if not report.all_ok:
            sys.exit(1)
        return

    _print_report(report)
    if not report.all_ok:
        sys.exit(1)


def cmd_sync_add(args: list[str]) -> None:
    """Add a secret to sync config."""
    config, config_path = _load_config(args)

    # Parse: banto sync add <name> --env <ENV_VAR> [--target platform:project ...]
    #         [--account <keychain_account>]  — reference existing Keychain entry, skip value input
    name = None
    env_name = None
    description = ""
    account = None
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
        elif args[i] == "--account" and i + 1 < len(args):
            account = args[i + 1]
            i += 2
        elif args[i] == "--config":
            i += 2  # skip
        elif args[i] == "--json":
            i += 1
        elif not args[i].startswith("--") and name is None:
            name = args[i]
            i += 1
        else:
            i += 1

    if not name or not env_name:
        print("Usage: banto sync add <name> --env <ENV_VAR> [--target platform:project] [--account <keychain_account>]")
        sys.exit(1)

    if config.get_secret(name):
        print(f"Error: Secret '{name}' already exists.")
        sys.exit(1)

    kc = KeychainStore(service_prefix=config.keychain_service)
    effective_account = account or name

    if account:
        # Reference existing Keychain entry — no value input needed
        # Verify the entry exists
        existing = kc.get(account)
        if existing is None:
            # Try without prefix (raw Keychain service name)
            import subprocess as sp
            raw_check = sp.run(
                ["security", "find-generic-password", "-s", account, "-w"],
                capture_output=True, text=True,
            )
            if raw_check.returncode != 0:
                print(f"Error: Keychain entry '{account}' not found.")
                sys.exit(1)
        value = None  # Don't need the value for config registration
    else:
        # Interactive: prompt for value
        value = getpass.getpass(f"Enter value for {name}: ")
        if not value:
            print("Empty value. Cancelled.")
            sys.exit(1)
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
        name=name, account=effective_account, env_name=env_name,
        description=description, targets=parsed_targets,
    )
    config.add_secret(entry)
    config.save(config_path)

    # Record history (only if we have a value — skip for --account references)
    if value:
        history = HistoryStore()
        ver = history.record(name, value, config.keychain_service)
        if ver is None:
            print("Warning: Failed to record version history (Keychain write failed).",
                  file=sys.stderr)

    source = f"account={effective_account}" if account else env_name
    print(f"Added '{name}' ({source}) with {len(parsed_targets)} target(s).")

    if parsed_targets:
        print("Syncing to targets...")
        report = sync_secret(config, name)
        _print_report(report)


def cmd_sync_audit(args: list[str]) -> None:
    """Check drift, staleness, fingerprint drift, and local file values."""
    config, _ = _load_config(args)
    from .sync_state import SyncState, fingerprint as fp

    max_age_days = None
    for i, a in enumerate(args):
        if a == "--max-age-days" and i + 1 < len(args):
            max_age_days = int(args[i + 1])

    kc = KeychainStore(service_prefix=config.keychain_service)
    entries = check_status(config)
    state = SyncState()
    issues: list[str] = []
    info: list[str] = []

    for entry in entries:
        name = entry.secret_name

        # 1. Existence drift
        if not entry.keychain_exists:
            issues.append(f"  DRIFT   {entry.env_name}: missing in Keychain")
            continue
        for label, status in entry.target_status.items():
            if status is False:
                issues.append(f"  DRIFT   {entry.env_name} -> {label}")

        # 2. Fingerprint drift (Keychain changed since last push?)
        secret_entry = config.get_secret(name)
        value = kc.get(secret_entry.account) if secret_entry else None
        if value:
            drift = state.check_drift(name, value)
            rec = state.get_push_record(name)
            if drift == "drift_local":
                pushed_at = rec.pushed_at[:10] if rec else "?"
                issues.append(
                    f"  DRIFT   {name}: Keychain changed since last push "
                    f"({fp(value)} != {rec.fingerprint}, pushed {pushed_at})"
                )
            elif drift == "never_pushed":
                issues.append(f"  DRIFT   {name}: never pushed (no sync record)")
            elif drift == "in_sync" and rec:
                info.append(f"  OK      {name}: fingerprint={fp(value)} pushed={rec.pushed_at[:10]}")

        # 3. Local file value comparison
        if secret_entry and value:
            for target in secret_entry.targets:
                if target.platform == "local" and target.file:
                    try:
                        content = Path(target.file).read_text(encoding="utf-8")
                        # Search for env_name=value in file
                        expected = f"{secret_entry.env_name}={value}"
                        if expected in content:
                            info.append(f"  MATCH   {name} -> {target.file}: value matches")
                        else:
                            # Check if key exists but value differs
                            if f"{secret_entry.env_name}=" in content:
                                issues.append(
                                    f"  MISMATCH {name} -> {target.file}: "
                                    f"file value differs from Keychain"
                                )
                    except OSError:
                        pass  # File not readable, existence already checked

    # 4. Rotation age check
    if max_age_days is not None:
        history = HistoryStore()
        now = datetime.now(timezone.utc)
        for name in config.secrets:
            versions = history.list_versions(name)
            if not versions:
                issues.append(f"  STALE   {name}: no version history (never rotated?)")
                continue
            latest = versions[-1]
            try:
                ts = datetime.fromisoformat(latest.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).days
                if age_days > max_age_days:
                    issues.append(
                        f"  STALE   {name}: last rotated {age_days}d ago "
                        f"(threshold: {max_age_days}d)"
                    )
            except (ValueError, TypeError):
                issues.append(f"  STALE   {name}: unparseable timestamp in history")

    # Output
    if _is_json(args):
        _json_out({"ok": len(issues) == 0, "issues": issues, "info": info})
        if issues:
            sys.exit(1)
        return

    if info:
        print("BANTO SYNC AUDIT\n")
        for line in info:
            print(line)
        print()

    if issues:
        print(f"{len(issues)} issue(s) found:\n")
        for issue in issues:
            print(issue)
        sys.exit(1)
    else:
        msg = "All secrets in sync."
        if max_age_days is not None:
            msg += f" No secrets older than {max_age_days} days."
        if not info:
            print("BANTO SYNC AUDIT\n")
        print(f"  {msg}")


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


def _resolve_new_value(args: list[str], name: str) -> str | None:
    """Resolve a new secret value from --from-cli or interactive prompt."""
    from_cli = None
    for i, a in enumerate(args):
        if a == "--from-cli" and i + 1 < len(args):
            from_cli = args[i + 1]
            break

    if from_cli:
        import shlex
        try:
            argv = shlex.split(from_cli)
        except ValueError as e:
            print(f"Error: Failed to parse command: {e}")
            return None
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            print(f"Error: Command not found: {argv[0]}")
            return None
        except subprocess.TimeoutExpired:
            print("Error: Command timed out (30s)")
            return None
        if result.returncode != 0:
            print(f"Error: Command failed (exit {result.returncode})")
            return None
        value = result.stdout.strip()
        if not value:
            print("Error: Command produced empty output")
            return None
        return value

    try:
        value = getpass.getpass(f"Enter new value for {name}: ")
        if not value:
            print("Empty value. Cancelled.")
            return None
        return value
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return None


def cmd_sync_rotate(args: list[str]) -> None:
    """Rotate a secret — update Keychain + re-sync all targets."""
    config, config_path = _load_config(args)

    name = None
    for a in args:
        if not a.startswith("--"):
            name = a
            break

    if not name:
        print("Usage: banto sync rotate <name> [--from-cli '<command>']")
        sys.exit(1)

    entry = config.get_secret(name)
    if entry is None:
        print(f"Error: Secret '{name}' not found.")
        sys.exit(1)

    value = _resolve_new_value(args, name)
    if value is None:
        sys.exit(1)

    # Update Keychain
    kc = KeychainStore(service_prefix=config.keychain_service)
    if not kc.store(entry.account, value):
        print("Error: Failed to update Keychain.")
        sys.exit(1)

    # Record history
    history = HistoryStore()
    new_ver = history.record(name, value, config.keychain_service)
    if new_ver is None:
        print("Error: Failed to record version history (Keychain write failed).",
              file=sys.stderr)
        sys.exit(1)
    print(f"Rotated '{name}' (now v{new_ver.version})")

    # Re-sync
    if entry.targets:
        print("Re-syncing to all targets...")
        report = sync_secret(config, name)
        _print_report(report)
        if not report.all_ok:
            sys.exit(1)


def cmd_sync_run(args: list[str]) -> None:
    """Run a command with sync secrets injected as environment variables."""
    config, _ = _load_config(args)
    kc = KeychainStore(service_prefix=config.keychain_service)

    env_name = None
    cmd_start = None
    for i, a in enumerate(args):
        if a == "--env" and i + 1 < len(args):
            env_name = args[i + 1]
        elif a == "--":
            cmd_start = i + 1
            break

    if cmd_start is None or cmd_start >= len(args):
        print("Usage: banto sync run [--env <env>] -- <command>")
        sys.exit(1)

    command = args[cmd_start:]

    # Resolve secrets (with environment inheritance if specified)
    if env_name:
        resolved = config.resolve_environment(env_name)
    else:
        resolved = dict(config.secrets)

    if not resolved:
        print("No secrets configured.")
        sys.exit(1)

    # Build env with secrets from Keychain
    env = os.environ.copy()
    loaded = 0
    for _name, entry in resolved.items():
        val = kc.get(entry.account)
        if val:
            env[entry.env_name] = val
            loaded += 1

    result = subprocess.run(command, env=env)
    sys.exit(result.returncode)


def cmd_sync_import(args: list[str]) -> None:
    """Import secrets from .env, .json, or .yaml file into Keychain + config."""
    config, config_path = _load_config(args)

    file_path = None
    for a in args:
        if not a.startswith("--"):
            file_path = Path(a)
            break

    if file_path is None:
        print("Usage: banto sync import <file>")
        sys.exit(1)

    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8")
    secrets: dict[str, str] = {}

    ext = file_path.suffix.lower()
    if ext == ".json":
        secrets = json.loads(content)
    else:
        # .env format
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if m:
                key, val = m.group(1), m.group(2)
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                secrets[key] = val

    if not secrets:
        print("No secrets found in file.")
        sys.exit(1)

    kc = KeychainStore(service_prefix=config.keychain_service)
    history = HistoryStore()
    count = 0

    for env_var, value in secrets.items():
        name = env_var.lower().replace("_", "-")
        if config.get_secret(name):
            print(f"  Skip: {name} (already exists)")
            continue

        if not kc.store(name, value):
            print(f"  Error: Failed to store {name}")
            continue

        entry = SecretEntry(name=name, account=name, env_name=env_var)
        config.add_secret(entry)
        ver = history.record(name, value, config.keychain_service)
        if ver is None:
            print(f"  Warning: Failed to record history for {name}", file=sys.stderr)
        count += 1

    config.save(config_path)
    print(f"Imported {count} secret(s) from {file_path.name}.")


def cmd_sync_validate(args: list[str]) -> None:
    """Validate API keys against provider endpoints.

    If sync.json has secrets, validates those.
    With --keychain flag, scans Keychain directly for known provider patterns.
    With --dry-run, shows which keys would be tested without sending them.
    """
    from .validate import validate_key, list_supported_providers, SERVICE_PATTERNS, should_exclude

    config, _ = _load_config(args)
    scan_keychain = "--keychain" in args
    dry_run = "--dry-run" in args

    keys_to_test: list[tuple[str, str]] = []  # (name, value)

    if not config.secrets and not scan_keychain:
        print("No secrets in sync.json. Use --keychain to scan Keychain.")
        return

    if scan_keychain:
        print("Warning: Scanning Keychain and sending keys to provider validation endpoints...")
        # Scan Keychain for known API key patterns
        import subprocess as sp
        result = sp.run(
            ["security", "dump-keychain"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            import re
            svce_re = re.compile(r'"svce"<blob>="([^"]*)"')
            acct_re = re.compile(r'"acct"<blob>="([^"]*)"')

            # Collect all service+account pairs from keychain dump
            entries_found: list[tuple[str, str]] = []
            current_attrs: dict[str, str] = {}
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("class:"):
                    if "svce" in current_attrs:
                        entries_found.append((
                            current_attrs.get("svce", ""),
                            current_attrs.get("acct", ""),
                        ))
                    current_attrs = {}
                    continue
                m = svce_re.search(stripped)
                if m:
                    current_attrs["svce"] = m.group(1)
                m = acct_re.search(stripped)
                if m:
                    current_attrs["acct"] = m.group(1)
            # Don't forget last entry
            if "svce" in current_attrs:
                entries_found.append((
                    current_attrs.get("svce", ""),
                    current_attrs.get("acct", ""),
                ))

            # Filter for known provider patterns and retrieve values
            seen: set[str] = set()
            for svc, acct in entries_found:
                if not svc or svc in seen or should_exclude(svc):
                    continue
                svc_lower = svc.lower()
                for pattern in SERVICE_PATTERNS:
                    if pattern in svc_lower:
                        seen.add(svc)
                        try:
                            val = sp.run(
                                ["security", "find-generic-password",
                                 "-s", svc, "-w"],
                                capture_output=True, text=True,
                            ).stdout.strip()
                            if val:
                                keys_to_test.append((svc, val))
                        except Exception:
                            pass
                        break
    else:
        # Use sync.json secrets
        kc = KeychainStore(service_prefix=config.keychain_service)
        for name, entry in config.secrets.items():
            value = kc.get(entry.account)
            if value:
                keys_to_test.append((name, value))
            else:
                keys_to_test.append((name, ""))

    if not keys_to_test:
        print("No keys found to validate.")
        print(f"  Supported providers: {', '.join(list_supported_providers())}")
        return

    if dry_run:
        print(f"\nBANTO SYNC VALIDATE — Dry run: {len(keys_to_test)} key(s) would be tested\n")
        for name, _value in keys_to_test:
            print(f"  WOULD TEST  {name}")
        print("\nNo keys were sent to provider endpoints.")
        return

    results_data: list[dict] = []
    all_valid = True

    if not _is_json(args):
        print(f"\nBANTO SYNC VALIDATE — Testing {len(keys_to_test)} key(s)\n")

    for name, value in keys_to_test:
        if not value:
            results_data.append({"name": name, "status": "skip", "message": "no value"})
            if not _is_json(args):
                print(f"  SKIP  {name}: no value")
            continue
        result = validate_key(name, value)
        results_data.append({
            "name": name, "provider": result.provider,
            "status": result.status, "message": result.message,
        })
        if result.status == "fail":
            all_valid = False
        if not _is_json(args):
            if result.status == "pass":
                print(f"  PASS    {name}: {result.message}")
            elif result.status == "fail":
                print(f"  FAIL    {name}: {result.message}")
            else:
                print(f"  UNKNOWN {name}: {result.message}")

    if _is_json(args):
        _json_out({"ok": all_valid, "results": results_data})
        if not all_valid:
            sys.exit(1)
        return

    print()
    if not all_valid:
        print("  Some keys are invalid.")
        sys.exit(1)
    else:
        print("  All testable keys valid.")


def cmd_sync_setup(args: list[str]) -> None:
    """Auto-detect env vars on a platform and match to Keychain entries."""
    from .setup import run_setup

    config, config_path = _load_config(args)
    dry_run = "--dry-run" in args
    guess = "--guess" in args

    # Parse platform:project
    target = None
    for a in args:
        if ":" in a and not a.startswith("--"):
            target = a
            break

    if not target:
        print("Usage: banto sync setup <platform:project> [--dry-run] [--guess] [--json]")
        print("Example: banto sync setup vercel:allnew-corporate")
        print("         banto sync setup cloudflare-pages:my-site --dry-run")
        print("         banto sync setup vercel:my-app --guess  # fallback to known env vars")
        sys.exit(1)

    platform, project = target.split(":", 1)

    print(f"\nBANTO SYNC SETUP — {platform}:{project}\n")
    if dry_run:
        print("  (dry run — no changes will be made)\n")
    if guess:
        print("  (guess mode — using known env var catalog as fallback)\n")

    matches = run_setup(
        platform=platform, project=project,
        config=config, config_path=config_path,
        dry_run=dry_run,
        guess=guess,
    )

    # Handle discovery_empty (fail-closed)
    if len(matches) == 1 and matches[0].status == "discovery_empty":
        if _is_json(args):
            _json_out({
                "platform": platform, "project": project, "dry_run": dry_run,
                "status": "discovery_empty",
                "matches": [],
            })
            sys.exit(1)
        print(f"  No env vars discovered on {platform}:{project}.")
        print(f"  This may indicate an auth issue, wrong project name, or empty project.")
        print(f"\n  To fall back to known env var catalog, re-run with --guess:")
        print(f"    banto sync setup {platform}:{project} --guess")
        sys.exit(1)

    if _is_json(args):
        _json_out({
            "platform": platform, "project": project, "dry_run": dry_run,
            "guess": guess,
            "matches": [
                {"env_var": m.env_var, "keychain": m.keychain_service, "status": m.status}
                for m in matches
            ],
        })
        return

    matched = [m for m in matches if m.status == "matched"]
    missing = [m for m in matches if m.status == "missing"]
    existing = [m for m in matches if m.status == "already_configured"]

    for m in matched:
        print(f"  MATCH  {m.env_var} -> {m.keychain_service}")
    for m in existing:
        print(f"  SKIP   {m.env_var} (already in sync.json)")
    for m in missing:
        print(f"  MISS   {m.env_var} (no Keychain match)")

    print()
    if matched and not dry_run:
        print(f"  Registered {len(matched)} secret(s) in sync.json.")
        print(f"  Run: banto sync push")
    elif matched and dry_run:
        print(f"  Would register {len(matched)} secret(s). Remove --dry-run to apply.")

    if missing:
        print(f"\n  {len(missing)} key(s) not found in Keychain:")
        for m in missing:
            name = m.env_var.lower().replace("_", "-")
            print(f"    banto register {name}")


SYNC_COMMANDS = {
    "status": cmd_sync_status,
    "push": cmd_sync_push,
    "add": cmd_sync_add,
    "rotate": cmd_sync_rotate,
    "audit": cmd_sync_audit,
    "validate": cmd_sync_validate,
    "history": cmd_sync_history,
    "run": cmd_sync_run,
    "export": cmd_sync_export,
    "import": cmd_sync_import,
    "init": cmd_sync_init,
    "setup": cmd_sync_setup,
    "ui": cmd_sync_ui,
}


def cmd_sync_dispatch(args: list[str]) -> None:
    """Dispatch banto sync <subcommand>."""
    if not args or args[0] in ("-h", "--help"):
        print("banto sync: Multi-platform secret sync\n")
        print("Usage: banto sync <command> [args]\n")
        print("Commands:")
        print("  setup <plat:proj>   Auto-detect env vars + match Keychain (one command)")
        print("  init                Create default sync config")
        print("  status              Show sync status matrix")
        print("  validate            Test API keys against provider endpoints")
        print("  push [--validate]   Sync secrets to targets (--validate first)")
        print("  add <name> ...      Add a new secret")
        print("  rotate <name>       Rotate a secret (update + re-sync)")
        print("  audit [--max-age-days N]  Check drift + fingerprint + stale")
        print("  history <name>      Show version history")
        print("  run [--env E] -- <cmd>  Run command with secrets as env vars")
        print("  export [--format]   Export secrets (env/json/docker)")
        print("  import <file>       Import from .env or .json file")
        print("  ui [--port N]       Launch local web UI")
        sys.exit(0)

    sub = args[0]
    if sub not in SYNC_COMMANDS:
        print(f"Unknown sync command: {sub}", file=sys.stderr)
        print(f"Available: {', '.join(SYNC_COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    SYNC_COMMANDS[sub](args[1:])

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

from ..keychain import KeychainStore, _ctypes_exists, _ctypes_get, default_keychain_account
from .capabilities import (
    ROTATION_CLASS_ORDER,
    classify_config,
    summarize_classifications,
)
from .browser_issuer import (
    BrowserIssuePlan,
    BrowserIssueResult,
    BrowserIssuerError,
    BrowserRetirementResult,
    build_browser_retirement_plan,
    build_browser_issue_plan,
    load_browser_retirement_recipe,
    issue_secret_with_browser,
    load_browser_issuer_recipe,
    retire_key_with_browser,
)
from .browser_recorder import (
    BrowserRecordingResult,
    build_browser_recording_plan,
    record_browser_recipe,
)
from .browser_batch import (
    BrowserBatchResult,
    load_browser_batch_plan,
    run_browser_batch,
)
from .cloudflare_token_rotator import (
    DEFAULT_CREATOR_ACCOUNT as DEFAULT_CLOUDFLARE_CREATOR_ACCOUNT,
    DEFAULT_CREATOR_TOKEN_ENV as DEFAULT_CLOUDFLARE_CREATOR_TOKEN_ENV,
    CloudflareTokenRotatorError,
    build_cloudflare_account_token_plan,
    load_cloudflare_token_policy,
    rotate_cloudflare_account_token,
)
from .config import SyncConfig, SecretEntry, Target, DEFAULT_CONFIG_PATH
from .history import HistoryStore
from .google_rotator import (
    DEFAULT_ACCESS_TOKEN_ENV,
    DEFAULT_ADC_COMMAND,
    DEFAULT_GCLOUD_AUTH_COMMAND,
    GoogleRotatorError,
    build_google_api_key_plan,
    rotate_google_api_key,
)
from .openai_rotator import (
    DEFAULT_ADMIN_ACCOUNT,
    DEFAULT_ADMIN_KEY_ENV,
    OpenAIRotatorError,
    build_openai_service_account_plan,
    delete_project_service_account,
    list_project_service_accounts,
    resolve_openai_admin_key,
    rotate_openai_service_account,
)
from .xai_rotator import (
    DEFAULT_MANAGEMENT_ACCOUNT as DEFAULT_XAI_MANAGEMENT_ACCOUNT,
    DEFAULT_MANAGEMENT_KEY_ENV as DEFAULT_XAI_MANAGEMENT_KEY_ENV,
    DEFAULT_XAI_ACLS,
    XAIRotatorError,
    build_xai_api_key_plan,
    rotate_xai_api_key,
)
from .incident_report import INCIDENT_LANE_ORDER, LANE_LABELS, build_incident_report
from .propagation import build_propagation_plan, propagate_secret, validate_propagation_plan
from .smoke_presets import list_smoke_presets, smoke_preset_exists
from .stripe_webhook_rotator import (
    StripeWebhookRotatorError,
    build_stripe_webhook_endpoint_plan,
    rotate_stripe_webhook_endpoint,
)
from .sync import SyncReport, check_status, sync_all, sync_secret, remove_secret
from .vercel_inventory import build_vercel_inventory_report, report_to_json


def _is_json(args: list[str]) -> bool:
    return "--json" in args


def _json_out(data: dict | list) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


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


def cmd_sync_classify(args: list[str]) -> None:
    """Classify sync-managed secrets by rotation capability."""
    config, _ = _load_config(args)
    classifications = classify_config(config)
    summary = summarize_classifications(classifications)

    if _is_json(args):
        _json_out({
            "count": len(classifications),
            "summary": summary,
            "secrets": [
                {
                    "name": item.secret_name,
                    "env_name": item.env_name,
                    "provider": item.provider,
                    "rotation_class": item.rotation_class,
                    "implementation_phase": item.implementation_phase,
                    "matched_rule": item.matched_rule,
                    "notes": item.notes,
                }
                for item in classifications
            ],
        })
        return

    if not classifications:
        print("BANTO SYNC CLASSIFY — No secrets configured.")
        return

    print(f"\nBANTO SYNC CLASSIFY — {len(classifications)} secret(s)\n")
    print("  Rotation class counts:\n")
    for rotation_class in ROTATION_CLASS_ORDER:
        print(f"  {rotation_class:<15} {summary.get(rotation_class, 0):>3}")

    print("\n  Details:\n")
    env_w = max(20, *(len(item.env_name) for item in classifications))
    provider_w = max(12, *(len(item.provider) for item in classifications))
    phase_w = max(8, *(len(item.implementation_phase) for item in classifications))

    for rotation_class in ROTATION_CLASS_ORDER:
        group = [item for item in classifications if item.rotation_class == rotation_class]
        if not group:
            continue
        print(f"  [{rotation_class}]")
        for item in group:
            print(
                f"    {item.env_name:<{env_w}}  "
                f"{item.provider:<{provider_w}}  "
                f"{item.implementation_phase:<{phase_w}}  "
                f"{item.notes}"
            )
        print()


def cmd_sync_incident_report(args: list[str]) -> None:
    """Build an incident-oriented rotation report from sync-managed secrets."""
    config, _ = _load_config(args)
    report = build_incident_report(config)
    counts = report.counts_by_lane()

    if _is_json(args):
        _json_out({
            "count": len(report.plans),
            "counts_by_lane": counts,
            "plans": [
                {
                    "secret_name": plan.secret_name,
                    "env_name": plan.env_name,
                    "provider": plan.provider,
                    "rotation_class": plan.rotation_class,
                    "implementation_phase": plan.implementation_phase,
                    "incident_lane": plan.incident_lane,
                    "incident_priority": plan.incident_priority,
                    "operator_action": plan.operator_action,
                    "notes": plan.notes,
                    "recommended_command": plan.recommended_command,
                    "requires_human_value": plan.requires_human_value,
                    "targets": list(plan.targets),
                    "shared_account_secret_names": list(plan.shared_account_secret_names),
                }
                for plan in report.plans
            ],
        })
        return

    if not report.plans:
        print("BANTO SYNC INCIDENT REPORT — No secrets configured.")
        return

    print(f"\nBANTO SYNC INCIDENT REPORT — {len(report.plans)} secret(s)\n")
    print("  Lane counts:\n")
    for lane in INCIDENT_LANE_ORDER:
        print(f"  {LANE_LABELS[lane]:<16} {counts.get(lane, 0):>3}")

    for lane in INCIDENT_LANE_ORDER:
        plans = report.by_lane(lane)
        if not plans:
            continue
        print(f"\n  [{LANE_LABELS[lane]}]")
        for plan in plans:
            print(
                f"    {plan.env_name} ({plan.secret_name})  "
                f"class={plan.rotation_class}  provider={plan.provider}"
            )
            print(f"    action:  {plan.operator_action}")
            print(f"    command: {plan.recommended_command}")
            if plan.targets:
                print(f"    targets: {', '.join(plan.targets)}")
            if plan.shared_account_secret_names:
                print(f"    shared:  {', '.join(plan.shared_account_secret_names)}")
            if plan.notes:
                print(f"    notes:   {plan.notes}")


def cmd_sync_vercel_inventory(args: list[str]) -> None:
    """Read-only inventory of Vercel env vars without values."""
    config, _ = _load_config(args)
    projects: list[str] = []
    exclude_envs: list[str] = []

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--project" and i + 1 < len(args):
            projects.append(args[i + 1])
            i += 2
            continue
        if arg == "--exclude-env" and i + 1 < len(args):
            exclude_envs.append(args[i + 1])
            i += 2
            continue
        if arg in {"--config", "--json"}:
            i += 2 if arg == "--config" else 1
            continue
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync vercel-inventory --project <name> "
                "[--project <name> ...] [--exclude-env ENV] [--json]"
            )
            return
        print(f"Error: Unknown option for vercel-inventory: {arg}", file=sys.stderr)
        sys.exit(1)

    if not projects:
        print(
            "Usage: banto sync vercel-inventory --project <name> "
            "[--project <name> ...] [--exclude-env ENV] [--json]"
        )
        sys.exit(1)

    report = build_vercel_inventory_report(
        config,
        projects,
        exclude_envs=exclude_envs,
    )

    if _is_json(args):
        _json_out(report_to_json(report))
        return

    counts = report.counts_by_lane()
    print(f"\nBANTO SYNC VERCEL INVENTORY — {len(report.items)} env var entrie(s)\n")
    print("  Lane counts:")
    print(f"  Rotate Now        {counts.get('rotate_now', 0):>3}")
    print(f"  Manual Cutover    {counts.get('manual_cutover', 0):>3}")
    print(f"  Monitor Only      {counts.get('monitor_only', 0):>3}")
    print(f"  Review Required   {counts.get('review_required', 0):>3}")
    print(f"  Excluded          {counts.get('excluded', 0):>3}")
    print(f"\n  Unmanaged by sync:     {report.unmanaged_count()}")
    print(f"  Needs sensitive flag:  {report.sensitive_upgrade_count()}")

    current_project = None
    for item in sorted(report.items, key=lambda x: (x.project, x.lane, x.env_name, x.targets)):
        if item.project != current_project:
            current_project = item.project
            print(f"\n  [{current_project}]")
        age = "?" if item.age_days is None else f"{item.age_days}d"
        managed = "managed" if item.managed_by_sync else "unmanaged"
        sensitive = "needs-sensitive" if item.needs_sensitive_upgrade else item.value_type
        targets = ",".join(item.targets) or "-"
        print(
            f"    {item.lane:<14} {item.env_name:<36} "
            f"{item.classification.provider:<14} {item.classification.rotation_class:<14} "
            f"{sensitive:<15} {managed:<9} {age:<5} {targets}"
        )


def _print_sync_push_usage() -> None:
    print("Usage: banto sync push [name] [--validate] [--json] [--config <path>]")


def _parse_sync_push_args(args: list[str]) -> tuple[str | None, bool] | None:
    """Parse sync push args before any secret reads or remote writes."""
    if "-h" in args or "--help" in args:
        _print_sync_push_usage()
        return None

    do_validate = False
    name: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--config":
            if i + 1 >= len(args):
                print("Error: --config requires a path.", file=sys.stderr)
                sys.exit(1)
            i += 2
            continue
        if arg == "--validate":
            do_validate = True
            i += 1
            continue
        if arg == "--json":
            i += 1
            continue
        if arg.startswith("--"):
            print(f"Error: Unknown option for sync push: {arg}", file=sys.stderr)
            _print_sync_push_usage()
            sys.exit(1)
        if name is not None:
            print("Error: sync push accepts at most one secret name.", file=sys.stderr)
            _print_sync_push_usage()
            sys.exit(1)
        name = arg
        i += 1

    return name, do_validate


def cmd_sync_push(args: list[str]) -> None:
    """Push secrets from Keychain to all targets."""
    parsed = _parse_sync_push_args(args)
    if parsed is None:
        return

    name, do_validate = parsed
    config, _ = _load_config(args)

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
            # Try without prefix (raw Keychain service name).
            # Use ctypes to avoid exposing values in process arguments.
            from ..keychain import _ctypes_get, default_keychain_account
            _acct = default_keychain_account()
            if _ctypes_get(account, _acct) is None:
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


def cmd_sync_import_keychain(args: list[str]) -> None:
    """Import an existing exact Keychain service into the configured sync account."""
    name = None
    from_service = None
    from_account = None
    from_account_empty = False
    do_push = "--push" in args
    dry_run = "--dry-run" in args

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync import-keychain <name> --from-service <service> "
                "[--from-account <account> | --from-account-empty] [--push] [--dry-run] [--json]"
            )
            return
        if arg == "--from-service" and i + 1 < len(args):
            from_service = args[i + 1]
            i += 2
            continue
        if arg == "--from-account" and i + 1 < len(args):
            from_account = args[i + 1]
            i += 2
            continue
        if arg == "--from-account-empty":
            from_account_empty = True
            i += 1
            continue
        if arg in {"--config"}:
            i += 2
            continue
        if arg in {"--push", "--dry-run", "--json"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not from_service:
        print(
            "Usage: banto sync import-keychain <name> --from-service <service> "
            "[--from-account <account> | --from-account-empty] [--push] [--dry-run] [--json]"
        )
        sys.exit(1)
    if from_account is not None and from_account_empty:
        print("Error: Specify either --from-account or --from-account-empty, not both.")
        sys.exit(1)

    config, _ = _load_config(args)
    entry = config.get_secret(name)
    if entry is None:
        print(f"Error: Secret '{name}' not found in sync config.")
        sys.exit(1)

    source_account = (
        ""
        if from_account_empty
        else (from_account if from_account is not None else default_keychain_account())
    )
    kc = KeychainStore(service_prefix=config.keychain_service)
    source_exists = _ctypes_exists(from_service, source_account)
    dest_exists = kc.exists(entry.account)

    if dry_run:
        payload = {
            "ok": source_exists,
            "dry_run": True,
            "name": entry.name,
            "env_name": entry.env_name,
            "from_service": from_service,
            "from_account": source_account,
            "to_service_prefix": config.keychain_service,
            "to_account": entry.account,
            "source_exists": source_exists,
            "destination_exists": dest_exists,
            "push": do_push,
            "targets": [target.label for target in entry.targets],
        }
        if _is_json(args):
            _json_out(payload)
            if not source_exists:
                sys.exit(1)
            return
        print(f"\nBANTO SYNC IMPORT KEYCHAIN — Dry run for {entry.name}\n")
        print(f"  from_service:       {from_service}")
        print(f"  from_account:       {source_account!r}")
        print(f"  to_service_prefix:  {config.keychain_service}")
        print(f"  to_account:         {entry.account}")
        print(f"  source_exists:      {'yes' if source_exists else 'no'}")
        print(f"  destination_exists: {'yes' if dest_exists else 'no'}")
        print(f"  push_after_import:  {'yes' if do_push else 'no'}")
        print(f"  targets:            {len(entry.targets)}")
        for target in entry.targets:
            print(f"    - {target.label}")
        if not source_exists:
            sys.exit(1)
        return

    value = _ctypes_get(from_service, source_account)
    if value is None:
        if _is_json(args):
            _json_out({
                "ok": False,
                "name": entry.name,
                "error": "source_not_found",
                "from_service": from_service,
                "from_account": source_account,
            })
        else:
            print("Error: Source Keychain item not found.")
        sys.exit(1)

    if not kc.store(entry.account, value):
        if _is_json(args):
            _json_out({"ok": False, "name": entry.name, "error": "destination_store_failed"})
        else:
            print("Error: Failed to store imported value in banto sync Keychain.")
        sys.exit(1)

    version = None
    history = HistoryStore()
    recorded = history.record(entry.name, value, config.keychain_service)
    if recorded is not None:
        version = recorded.version

    report = None
    if do_push:
        report = sync_secret(config, entry.name)

    if _is_json(args):
        _json_out({
            "ok": report.fail_count == 0 if report is not None else True,
            "name": entry.name,
            "env_name": entry.env_name,
            "from_service": from_service,
            "from_account": source_account,
            "to_service_prefix": config.keychain_service,
            "to_account": entry.account,
            "version": version,
            "push": do_push,
            "sync": None if report is None else {
                "ok_count": report.ok_count,
                "fail_count": report.fail_count,
                "results": [
                    {
                        "secret_name": item.secret_name,
                        "target_label": item.target_label,
                        "success": item.success,
                        "message": item.message,
                    }
                    for item in report.results
                ],
            },
        })
        if report is not None and report.fail_count:
            sys.exit(1)
        return

    version_label = f" (now v{version})" if version is not None else ""
    print(f"Imported '{entry.name}' into banto sync Keychain{version_label}.")
    if report is not None:
        print("Syncing to targets...")
        _print_report(report)
        if report.fail_count:
            sys.exit(1)


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


def _parse_smoke_command(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "--smoke" and i + 1 < len(args):
            return args[i + 1]
    return None


def _parse_smoke_preset(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "--smoke-preset" and i + 1 < len(args):
            return args[i + 1]
    return None


def _parse_smoke_options(args: list[str]) -> tuple[str | None, str | None]:
    smoke_command = _parse_smoke_command(args)
    smoke_preset = _parse_smoke_preset(args)
    if smoke_command and smoke_preset:
        print("Error: Specify either --smoke or --smoke-preset, not both.")
        sys.exit(1)
    if smoke_preset and not smoke_preset_exists(smoke_preset):
        available = ", ".join(preset.name for preset in list_smoke_presets())
        print(f"Error: Unknown smoke preset '{smoke_preset}'. Available: {available}")
        sys.exit(1)
    return smoke_command, smoke_preset


def _format_smoke_label(smoke_command: str | None, smoke_preset: str | None) -> str | None:
    if smoke_preset:
        return f"preset:{smoke_preset}"
    return smoke_command


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        print(f"Error: {label} must be an integer.")
        sys.exit(1)
    if parsed <= 0:
        print(f"Error: {label} must be greater than zero.")
        sys.exit(1)
    return parsed


def _parse_key_value_options(args: list[str], flag: str) -> dict[str, str]:
    values: dict[str, str] = {}
    i = 0
    while i < len(args):
        if args[i] == flag and i + 1 < len(args):
            raw = args[i + 1]
            if "=" not in raw:
                print(f"Error: {flag} expects key=value.")
                sys.exit(1)
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value:
                print(f"Error: {flag} expects non-empty key=value.")
                sys.exit(1)
            values[key] = value
            i += 2
            continue
        i += 1
    return values


def _load_exposure_manifest(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: Failed to load exposure manifest: {exc}")
        sys.exit(1)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        print("Error: Exposure manifest must be a version 1 JSON object.")
        sys.exit(1)
    result: dict[str, str] = {}
    for key in ("key_id", "key_label", "revoke_recipe"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _propagation_json_payload(result) -> dict:
    return {
        "ok": result.ok,
        "name": result.plan.secret_name,
        "env_name": result.plan.env_name,
        "provider": result.plan.provider,
        "rotation_class": result.plan.rotation_class,
        "implementation_phase": result.plan.implementation_phase,
        "version": result.version,
        "validation": None if result.validation is None else {
            "provider": result.validation.provider,
            "status": result.validation.status,
            "message": result.validation.message,
        },
        "smoke_check": None if result.smoke_check is None else {
            "type": "preset" if result.smoke_check.command.startswith("preset:") else "command",
            "success": result.smoke_check.success,
            "exit_code": result.smoke_check.exit_code,
            "command": result.smoke_check.command,
            "message": result.smoke_check.message,
        },
        "sync": None if result.sync_report is None else {
            "ok": result.sync_report.all_ok,
            "ok_count": result.sync_report.ok_count,
            "fail_count": result.sync_report.fail_count,
            "results": [
                {
                    "target": item.target_label,
                    "success": item.success,
                    "message": item.message,
                }
                for item in result.sync_report.results
            ],
        },
    }


def _browser_step_payload(plan: BrowserIssuePlan) -> list[dict[str, object]]:
    return [
        {
            "action": step.action,
            "selector": step.selector,
            "has_text_template": step.text is not None,
            "key": step.key,
            "has_value": step.value is not None,
            "has_url": step.url is not None,
            "state": step.state,
            "timeout_ms": step.timeout_ms,
            "message": step.message,
        }
        for step in plan.recipe.steps
    ]


def _browser_issue_json_payload(result: BrowserIssueResult) -> dict:
    return {
        "ok": result.ok,
        "name": result.plan.propagation_plan.secret_name,
        "env_name": result.plan.propagation_plan.env_name,
        "provider": result.plan.propagation_plan.provider,
        "rotation_class": result.plan.propagation_plan.rotation_class,
        "implementation_phase": result.plan.propagation_plan.implementation_phase,
        "recipe": {
            "name": result.plan.recipe.name,
            "provider": result.plan.recipe.provider,
            "start_url": result.plan.recipe.start_url,
            "steps": _browser_step_payload(result.plan),
            "capture": {
                "selector": result.plan.recipe.capture.selector,
                "source": result.plan.recipe.capture.source,
                "attribute": result.plan.recipe.capture.attribute,
                "has_regex": result.plan.recipe.capture.regex is not None,
                "min_length": result.plan.recipe.capture.min_length,
            },
            "metadata_selectors": list(result.plan.recipe.metadata_selectors),
        },
        "profile_dir": str(result.plan.profile_dir),
        "headless": result.plan.headless,
        "allow_manual_cutover": result.plan.allow_manual_cutover,
        "metadata": result.metadata,
        "propagation": None
        if result.propagation is None else _propagation_json_payload(result.propagation),
        "retirement": None
        if result.retirement is None else _browser_retirement_json_payload(result.retirement),
        "error": result.error,
    }


def _browser_retirement_json_payload(result: BrowserRetirementResult) -> dict:
    return {
        "ok": result.ok,
        "name": result.plan.propagation_plan.secret_name,
        "env_name": result.plan.propagation_plan.env_name,
        "provider": result.plan.propagation_plan.provider,
        "recipe": {
            "name": result.plan.recipe.name,
            "provider": result.plan.recipe.provider,
            "start_url": result.plan.recipe.start_url,
            "steps": [
                {
                    "action": step.action,
                    "selector": step.selector,
                    "has_text_template": step.text is not None,
                    "key": step.key,
                    "has_value": step.value is not None,
                    "has_url": step.url is not None,
                    "state": step.state,
                    "timeout_ms": step.timeout_ms,
                    "message": step.message,
                }
                for step in result.plan.recipe.steps
            ],
            "success_selector": result.plan.recipe.success_selector,
            "success_url": result.plan.recipe.success_url,
        },
        "key_id": result.plan.key_id,
        "key_label": result.plan.key_label,
        "profile_dir": str(result.plan.profile_dir),
        "headless": result.plan.headless,
        "retired": result.retired,
        "message": result.message,
        "error": result.error,
    }


def _browser_recording_json_payload(result: BrowserRecordingResult) -> dict:
    return {
        "ok": result.ok,
        "provider": result.provider,
        "start_url": result.start_url,
        "recipe_path": str(result.recipe_path),
        "script_path": None if result.script_path is None else str(result.script_path),
        "exposure_manifest_path": None
        if result.exposure_manifest_path is None else str(result.exposure_manifest_path),
        "action_count": result.action_count,
        "capture_selector": result.capture_selector,
        "metadata_keys": list(result.metadata_keys),
        "exposed_key_id_recorded": result.exposed_key_id_recorded,
        "warnings": list(result.warnings),
        "error": result.error,
    }


def _browser_batch_json_payload(result: BrowserBatchResult) -> dict:
    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "name": result.plan.name,
        "fail_fast": result.plan.fail_fast,
        "items": [
            {
                "name": outcome.name,
                "ok": outcome.ok,
                "skipped": outcome.skipped,
                "dry_run": outcome.dry_run,
                "provider": outcome.provider,
                "recipe": outcome.recipe_name,
                "targets": list(outcome.targets),
                "retirement_planned": outcome.retirement_planned,
                "revoke_key_id": outcome.revoke_key_id,
                "propagation": None
                if outcome.result is None or outcome.result.propagation is None
                else _propagation_json_payload(outcome.result.propagation),
                "retirement": None
                if outcome.result is None or outcome.result.retirement is None
                else _browser_retirement_json_payload(outcome.result.retirement),
                "error": outcome.error,
            }
            for outcome in result.outcomes
        ],
    }


def _cloudflare_rotation_json_payload(result) -> dict:
    return {
        "ok": result.ok,
        "name": result.plan.propagation_plan.secret_name,
        "env_name": result.plan.propagation_plan.env_name,
        "account_id": result.plan.account_id,
        "token_name": result.plan.token_name,
        "creator_token_source": result.creator_token_source,
        "created": None if result.created is None else {
            "token_id": result.created.token_id,
            "token_name": result.created.token_name,
            "expires_on": result.created.expires_on,
        },
        "propagation": None
        if result.propagation is None else _propagation_json_payload(result.propagation),
        "cleanup_of_created_token": None
        if result.cleanup_of_created_token is None else {
            "token_id": result.cleanup_of_created_token.token_id,
            "deleted": result.cleanup_of_created_token.deleted,
            "message": result.cleanup_of_created_token.message,
        },
        "revoked_previous_token": None
        if result.revoked_previous_token is None else {
            "token_id": result.revoked_previous_token.token_id,
            "deleted": result.revoked_previous_token.deleted,
            "message": result.revoked_previous_token.message,
        },
        "error": result.error,
    }


def _stripe_webhook_rotation_json_payload(result) -> dict:
    return {
        "ok": result.ok,
        "name": result.plan.propagation_plan.secret_name,
        "env_name": result.plan.propagation_plan.env_name,
        "source_secret_name": result.plan.source_secret_name,
        "url": result.plan.url,
        "enabled_events": list(result.plan.enabled_events),
        "stripe_key_source": result.stripe_key_source,
        "created": None if result.created is None else {
            "endpoint_id": result.created.endpoint_id,
            "livemode": result.created.livemode,
            "status": result.created.status,
        },
        "propagation": None
        if result.propagation is None else _propagation_json_payload(result.propagation),
        "cleanup_of_created_endpoint": None
        if result.cleanup_of_created_endpoint is None else {
            "endpoint_id": result.cleanup_of_created_endpoint.endpoint_id,
            "deleted": result.cleanup_of_created_endpoint.deleted,
            "message": result.cleanup_of_created_endpoint.message,
        },
        "deleted_previous_endpoint": None
        if result.deleted_previous_endpoint is None else {
            "endpoint_id": result.deleted_previous_endpoint.endpoint_id,
            "deleted": result.deleted_previous_endpoint.deleted,
            "message": result.deleted_previous_endpoint.message,
        },
        "error": result.error,
    }


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


def cmd_sync_propagate(args: list[str]) -> None:
    """Common propagation flow for propagate_only/full_auto/partial_auto secrets."""
    config, _ = _load_config(args)

    name = None
    for a in args:
        if not a.startswith("--"):
            name = a
            break

    if not name:
        print(
            "Usage: banto sync propagate <name> [--from-cli '<command>'] "
            "[--validate] [--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    try:
        plan = build_propagation_plan(config, name)
    except KeyError:
        print(f"Error: Secret '{name}' not found.")
        sys.exit(1)

    try:
        validate_propagation_plan(plan)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    smoke_command, smoke_preset = _parse_smoke_options(args)
    do_validate = "--validate" in args
    dry_run = "--dry-run" in args
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)

    if dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.secret_name,
            "env_name": plan.env_name,
            "provider": plan.provider,
            "rotation_class": plan.rotation_class,
            "implementation_phase": plan.implementation_phase,
            "matched_rule": plan.matched_rule,
            "targets": list(plan.targets),
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "notes": plan.notes,
        }
        if _is_json(args):
            _json_out(payload)
            return

        print(f"\nBANTO SYNC PROPAGATE — Dry run for {plan.secret_name}\n")
        print(f"  env_name:      {plan.env_name}")
        print(f"  provider:      {plan.provider}")
        print(f"  class:         {plan.rotation_class}")
        print(f"  phase:         {plan.implementation_phase}")
        print(f"  matched_rule:  {plan.matched_rule or '(none)'}")
        print(f"  validate:      {'yes' if do_validate else 'no'}")
        print(f"  smoke:         {smoke_label or '(none)'}")
        print(f"  targets:       {len(plan.targets)}")
        for label in plan.targets:
            print(f"    - {label}")
        if plan.notes:
            print(f"\n  notes: {plan.notes}")
        return

    value = _resolve_new_value(args, name)
    if value is None:
        sys.exit(1)

    result = propagate_secret(
        config,
        name,
        value,
        do_validate=do_validate,
        smoke_command=smoke_command,
        smoke_preset=smoke_preset,
    )

    if _is_json(args):
        _json_out(_propagation_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    if result.validation is not None:
        if result.validation.status == "pass":
            print(f"Validation passed: {result.validation.message}")
        elif result.validation.status == "unknown":
            print(f"Validation unknown: {result.validation.message}")
        else:
            print(f"Validation failed: {result.validation.message}")

    if result.version is not None:
        print(f"Propagated '{result.plan.secret_name}' (now v{result.version})")

    if result.sync_report is not None:
        print("Syncing to all targets...")
        _print_report(result.sync_report)

    if result.smoke_check is not None:
        if result.smoke_check.success:
            print(f"Smoke test passed: {result.smoke_check.command}")
        else:
            print(f"Smoke test failed: {result.smoke_check.message}")

    if not result.ok:
        sys.exit(1)


def cmd_sync_browser_record(args: list[str]) -> None:
    """Record browser actions into a reusable browser issuance recipe."""
    name = None
    provider = None
    start_url = None
    output_path = None
    profile_dir = None
    capture_selector = None
    capture_source = "text"
    capture_attribute = None
    capture_regex = None
    capture_min_length = 8
    script_out = None
    exposed_key_id_selector = None
    exposed_key_label_selector = None
    exposed_key_id = None
    exposed_key_label = None
    exposure_manifest_out = None
    revoke_recipe_path = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync browser-record <name> --start-url <url> "
                "[--provider <provider>] --output <recipe.json> "
                "[--capture-selector <selector> | --capture-from-last-click] "
                "[--metadata key=selector] [--script-out <path>] "
                "[--exposed-key-id <id> | --exposed-key-id-selector <selector>] "
                "[--exposure-manifest-out <path> --revoke-recipe <recipe.json>] "
                "[--dry-run] [--json]"
            )
            return
        if arg == "--provider" and i + 1 < len(args):
            provider = args[i + 1]
            i += 2
            continue
        if arg == "--start-url" and i + 1 < len(args):
            start_url = args[i + 1]
            i += 2
            continue
        if arg == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
            continue
        if arg == "--profile-dir" and i + 1 < len(args):
            profile_dir = args[i + 1]
            i += 2
            continue
        if arg == "--capture-selector" and i + 1 < len(args):
            capture_selector = args[i + 1]
            i += 2
            continue
        if arg == "--capture-source" and i + 1 < len(args):
            capture_source = args[i + 1]
            i += 2
            continue
        if arg == "--capture-attribute" and i + 1 < len(args):
            capture_attribute = args[i + 1]
            i += 2
            continue
        if arg == "--capture-regex" and i + 1 < len(args):
            capture_regex = args[i + 1]
            i += 2
            continue
        if arg == "--min-length" and i + 1 < len(args):
            capture_min_length = _parse_positive_int(args[i + 1], "--min-length")
            i += 2
            continue
        if arg == "--script-out" and i + 1 < len(args):
            script_out = args[i + 1]
            i += 2
            continue
        if arg == "--exposed-key-id-selector" and i + 1 < len(args):
            exposed_key_id_selector = args[i + 1]
            i += 2
            continue
        if arg == "--exposed-key-label-selector" and i + 1 < len(args):
            exposed_key_label_selector = args[i + 1]
            i += 2
            continue
        if arg == "--exposed-key-id" and i + 1 < len(args):
            exposed_key_id = args[i + 1]
            i += 2
            continue
        if arg == "--exposed-key-label" and i + 1 < len(args):
            exposed_key_label = args[i + 1]
            i += 2
            continue
        if arg == "--exposure-manifest-out" and i + 1 < len(args):
            exposure_manifest_out = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-recipe" and i + 1 < len(args):
            revoke_recipe_path = args[i + 1]
            i += 2
            continue
        if arg in {"--metadata", "--config"}:
            i += 2
            continue
        if arg in {"--headless", "--capture-from-last-click", "--dry-run", "--json"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not start_url:
        print(
            "Usage: banto sync browser-record <name> --start-url <url> "
            "[--provider <provider>] --output <recipe.json>"
        )
        sys.exit(1)

    try:
        config, _ = _load_config(args)
        plan = build_browser_recording_plan(
            config,
            name,
            start_url=start_url,
            provider=provider,
            output_path=output_path,
            profile_dir=profile_dir,
            headless="--headless" in args,
            capture_selector=capture_selector,
            capture_source=capture_source,
            capture_attribute=capture_attribute,
            capture_regex=capture_regex,
            capture_min_length=capture_min_length,
            capture_from_last_click="--capture-from-last-click" in args,
            metadata_selectors=_parse_key_value_options(args, "--metadata"),
            script_out=script_out,
            exposed_key_id_selector=exposed_key_id_selector,
            exposed_key_label_selector=exposed_key_label_selector,
            exposed_key_id=exposed_key_id,
            exposed_key_label=exposed_key_label,
            exposure_manifest_out=exposure_manifest_out,
            revoke_recipe_path=revoke_recipe_path,
        )
    except (BrowserIssuerError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if "--dry-run" in args:
        payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.secret_name,
            "provider": plan.provider,
            "start_url": plan.start_url,
            "recipe_path": str(plan.output_path),
            "profile_dir": str(plan.profile_dir),
            "capture_selector": plan.capture_selector,
            "capture_from_last_click": plan.capture_from_last_click,
            "metadata_keys": sorted(plan.metadata_selectors),
            "script_path": None if plan.script_out is None else str(plan.script_out),
            "exposure_manifest_path": None
            if plan.exposure_manifest_out is None else str(plan.exposure_manifest_out),
        }
        if _is_json(args):
            _json_out(payload)
            return
        print(f"\nBANTO SYNC BROWSER RECORD — Dry run for {plan.secret_name}\n")
        print(f"  provider:      {plan.provider}")
        print(f"  start_url:     {plan.start_url}")
        print(f"  recipe_path:   {plan.output_path}")
        print(f"  profile_dir:   {plan.profile_dir}")
        print(f"  capture:       {plan.capture_selector or '(last click)'}")
        print(f"  metadata:      {len(plan.metadata_selectors)}")
        print("\n  No browser was launched and no recipe was written.")
        return

    result = record_browser_recipe(plan)
    if _is_json(args):
        _json_out(_browser_recording_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    if not result.ok:
        print(f"Error: {result.error}")
        sys.exit(1)
    print(f"Recorded browser recipe: {result.recipe_path}")
    print(f"  actions:          {result.action_count}")
    print(f"  capture_selector: {result.capture_selector}")
    if result.script_path is not None:
        print(f"  script:           {result.script_path}")
    if result.exposure_manifest_path is not None:
        print(f"  exposure_manifest:{result.exposure_manifest_path}")
    for warning in result.warnings:
        print(f"  Warning: {warning}")


def cmd_sync_browser_revoke(args: list[str]) -> None:
    """Retire a provider credential through a local browser recipe."""
    name = None
    recipe_path = None
    profile_dir = None
    key_id = None
    key_label = None
    manifest_path = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync browser-revoke <name> "
                "--recipe <recipe.json> --key-id <provider-key-id> "
                "[--key-label <label>] [--profile-dir <dir>] "
                "[--headless] [--dry-run] [--json]"
            )
            return
        if arg == "--recipe" and i + 1 < len(args):
            recipe_path = args[i + 1]
            i += 2
            continue
        if arg == "--profile-dir" and i + 1 < len(args):
            profile_dir = args[i + 1]
            i += 2
            continue
        if arg == "--key-id" and i + 1 < len(args):
            key_id = args[i + 1]
            i += 2
            continue
        if arg == "--key-label" and i + 1 < len(args):
            key_label = args[i + 1]
            i += 2
            continue
        if arg == "--exposure-manifest" and i + 1 < len(args):
            manifest_path = args[i + 1]
            i += 2
            continue
        if arg in {"--config"}:
            i += 2
            continue
        if arg in {"--headless", "--dry-run", "--json"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    manifest = _load_exposure_manifest(manifest_path)
    recipe_path = recipe_path or manifest.get("revoke_recipe")
    key_id = key_id or manifest.get("key_id")
    key_label = key_label or manifest.get("key_label")

    if not name or not recipe_path or not key_id:
        print(
            "Usage: banto sync browser-revoke <name> "
            "--recipe <recipe.json> --key-id <provider-key-id>"
        )
        sys.exit(1)

    try:
        config, _ = _load_config(args)
        recipe = load_browser_retirement_recipe(recipe_path)
        plan = build_browser_retirement_plan(
            config,
            name,
            recipe,
            key_id=key_id,
            key_label=key_label,
            profile_dir=profile_dir,
            headless="--headless" in args,
        )
    except (BrowserIssuerError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if "--dry-run" in args:
        result = BrowserRetirementResult(plan=plan, retired=True, message="dry_run")
        payload = _browser_retirement_json_payload(result)
        payload.update({"ok": True, "dry_run": True})
        if _is_json(args):
            _json_out(payload)
            return
        print(f"\nBANTO SYNC BROWSER REVOKE — Dry run for {name}\n")
        print(f"  recipe:      {plan.recipe.name}")
        print(f"  provider:    {plan.propagation_plan.provider}")
        print(f"  key_id:      {plan.key_id}")
        print(f"  key_label:   {plan.key_label or '(none)'}")
        print(f"  profile_dir: {plan.profile_dir}")
        print("\n  No browser was launched and no credential was retired.")
        return

    result = retire_key_with_browser(
        config,
        name,
        recipe,
        key_id=key_id,
        key_label=key_label,
        profile_dir=profile_dir,
        headless="--headless" in args,
    )
    if _is_json(args):
        _json_out(_browser_retirement_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return
    if result.ok:
        print(f"Retired browser-managed credential: {result.plan.key_id}")
    else:
        print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_browser_batch(args: list[str]) -> None:
    """Run a batch of closed-loop browser key rotations."""
    plan_path = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync browser-batch <plan.json> "
                "[--dry-run] [--json]"
            )
            return
        if arg in {"--config"}:
            i += 2
            continue
        if arg in {"--dry-run", "--json"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if plan_path is None:
            plan_path = arg
        i += 1

    if not plan_path:
        print("Usage: banto sync browser-batch <plan.json> [--dry-run] [--json]")
        sys.exit(1)

    try:
        config, _ = _load_config(args)
        plan = load_browser_batch_plan(plan_path)
        result = run_browser_batch(
            config,
            plan,
            dry_run="--dry-run" in args,
        )
    except (BrowserIssuerError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out(_browser_batch_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    mode = "Dry run" if result.dry_run else "Run"
    print(f"\nBANTO SYNC BROWSER BATCH — {mode}: {result.plan.name}\n")
    for outcome in result.outcomes:
        if outcome.skipped:
            print(f"  SKIP  {outcome.name}")
            continue
        mark = "OK" if outcome.ok else "FAIL"
        retire = " + revoke" if outcome.retirement_planned else ""
        print(f"  {mark:<4} {outcome.name}{retire}")
        if outcome.recipe_name:
            print(f"       recipe:  {outcome.recipe_name}")
        if outcome.targets:
            print(f"       targets: {', '.join(outcome.targets)}")
        if outcome.error:
            print(f"       error:   {outcome.error}")
    if not result.ok:
        sys.exit(1)


def cmd_sync_browser_issue(args: list[str]) -> None:
    """Issue a provider credential through a local browser recipe."""
    name = None
    recipe_path = None
    profile_dir = None
    revoke_recipe_path = None
    revoke_profile_dir = None
    revoke_key_id = None
    revoke_key_label = None
    exposure_manifest_path = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync browser-issue <name> --recipe <recipe.json> "
                "[--profile-dir <dir>] [--headless] [--validate] "
                "[--smoke '<command>' | --smoke-preset <name>] "
                "[--revoke-recipe <recipe.json> --revoke-key-id <id>] "
                "[--exposure-manifest <path>] [--allow-manual-cutover] "
                "[--dry-run] [--json]"
            )
            return
        if arg == "--recipe" and i + 1 < len(args):
            recipe_path = args[i + 1]
            i += 2
            continue
        if arg == "--profile-dir" and i + 1 < len(args):
            profile_dir = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-recipe" and i + 1 < len(args):
            revoke_recipe_path = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-profile-dir" and i + 1 < len(args):
            revoke_profile_dir = args[i + 1]
            i += 2
            continue
        if arg in {"--revoke-key-id", "--revoke-exposed-key", "--revoke-previous-key"} and i + 1 < len(args):
            revoke_key_id = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-key-label" and i + 1 < len(args):
            revoke_key_label = args[i + 1]
            i += 2
            continue
        if arg in {"--exposure-manifest", "--revoke-exposed-manifest"} and i + 1 < len(args):
            exposure_manifest_path = args[i + 1]
            i += 2
            continue
        if arg in {"--config", "--smoke", "--smoke-preset"}:
            i += 2
            continue
        if arg in {
            "--json",
            "--dry-run",
            "--headless",
            "--validate",
            "--revoke-headless",
            "--allow-manual-cutover",
        }:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not recipe_path:
        print(
            "Usage: banto sync browser-issue <name> --recipe <recipe.json> "
            "[--profile-dir <dir>] [--headless] [--validate] "
            "[--smoke '<command>' | --smoke-preset <name>] "
            "[--revoke-recipe <recipe.json> --revoke-key-id <id>] "
            "[--exposure-manifest <path>] [--allow-manual-cutover] "
            "[--dry-run] [--json]"
        )
        sys.exit(1)

    dry_run = "--dry-run" in args
    headless = "--headless" in args
    do_validate = "--validate" in args
    allow_manual_cutover = "--allow-manual-cutover" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)
    manifest = _load_exposure_manifest(exposure_manifest_path)
    revoke_recipe_path = revoke_recipe_path or manifest.get("revoke_recipe")
    revoke_key_id = revoke_key_id or manifest.get("key_id")
    revoke_key_label = revoke_key_label or manifest.get("key_label")

    try:
        config, _ = _load_config(args)
        recipe = load_browser_issuer_recipe(recipe_path)
        plan = build_browser_issue_plan(
            config,
            name,
            recipe,
            profile_dir=profile_dir,
            headless=headless,
            allow_manual_cutover=allow_manual_cutover,
        )
        retire_recipe = (
            load_browser_retirement_recipe(revoke_recipe_path)
            if revoke_recipe_path else None
        )
        if revoke_key_id and retire_recipe is None:
            raise BrowserIssuerError(
                "Browser issue cannot revoke an exposed key without --revoke-recipe "
                "or an exposure manifest containing revoke_recipe."
            )
        if retire_recipe is not None:
            if not revoke_key_id:
                raise BrowserIssuerError(
                    "Browser issue retirement requires --revoke-key-id, "
                    "--revoke-exposed-key, or an exposure manifest key_id."
                )
            build_browser_retirement_plan(
                config,
                name,
                retire_recipe,
                key_id=revoke_key_id,
                key_label=revoke_key_label,
                profile_dir=revoke_profile_dir,
                headless="--revoke-headless" in args or headless,
            )
    except (BrowserIssuerError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        result = BrowserIssueResult(
            plan=plan,
            propagation=None,
            metadata={},
            error=None,
        )
        payload = _browser_issue_json_payload(result)
        payload.update({
            "ok": True,
            "dry_run": True,
            "validate": do_validate,
            "allow_manual_cutover": allow_manual_cutover,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
            "retirement": None if retire_recipe is None else {
                "recipe": retire_recipe.name,
                "key_id": revoke_key_id,
                "key_label": revoke_key_label,
                "profile_dir": revoke_profile_dir,
                "headless": "--revoke-headless" in args or headless,
            },
        })
        if _is_json(args):
            _json_out(payload)
            return

        print(f"\nBANTO SYNC BROWSER ISSUE — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:      {plan.propagation_plan.env_name}")
        print(f"  provider:      {plan.propagation_plan.provider}")
        print(f"  class:         {plan.propagation_plan.rotation_class}")
        print(f"  recipe:        {plan.recipe.name}")
        print(f"  start_url:     {plan.recipe.start_url}")
        print(f"  profile_dir:   {plan.profile_dir}")
        print(f"  headless:      {'yes' if headless else 'no'}")
        print(f"  validate:      {'yes' if do_validate else 'no'}")
        print(f"  manual_cutover:{' yes' if allow_manual_cutover else ' no'}")
        print(f"  smoke:         {smoke_label or '(none)'}")
        print(f"  revoke_after:  {retire_recipe.name if retire_recipe is not None else '(none)'}")
        print(f"  steps:         {len(plan.recipe.steps)}")
        print(f"  targets:       {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        print("\n  No browser was launched and no secret was captured.")
        return

    result = issue_secret_with_browser(
        config,
        name,
        recipe,
        profile_dir=profile_dir,
        headless=headless,
        do_validate=do_validate,
        smoke_command=smoke_command,
        smoke_preset=smoke_preset,
        retire_recipe=retire_recipe,
        retire_key_id=revoke_key_id,
        retire_key_label=revoke_key_label,
        retire_profile_dir=revoke_profile_dir,
        retire_headless="--revoke-headless" in args or headless,
        allow_manual_cutover=allow_manual_cutover,
    )

    if _is_json(args):
        _json_out(_browser_issue_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    if result.metadata:
        print("Captured non-secret metadata:")
        for key, value in result.metadata.items():
            print(f"  {key}: {value}")

    if result.propagation is not None:
        if result.propagation.validation is not None:
            if result.propagation.validation.status == "pass":
                print(f"Validation passed: {result.propagation.validation.message}")
            elif result.propagation.validation.status == "unknown":
                print(f"Validation unknown: {result.propagation.validation.message}")
            else:
                print(f"Validation failed: {result.propagation.validation.message}")
        if result.propagation.version is not None:
            print(
                f"Propagated '{result.propagation.plan.secret_name}' "
                f"(now v{result.propagation.version})"
            )
        if result.propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.propagation.sync_report)
        if result.propagation.smoke_check is not None:
            if result.propagation.smoke_check.success:
                print(f"Smoke test passed: {result.propagation.smoke_check.command}")
            else:
                print(f"Smoke test failed: {result.propagation.smoke_check.message}")

    if result.retirement is not None:
        if result.retirement.ok:
            print(f"Retired exposed/previous credential: {result.retirement.plan.key_id}")
        else:
            print(f"Retirement failed: {result.retirement.error}")

    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_quicktrust_credential(args: list[str]) -> None:
    """Issue a QuickTrust dashboard credential through a browser recipe."""
    if any(arg in {"-h", "--help"} for arg in args):
        print(
            "Usage: banto sync quicktrust-credential <name> --recipe <recipe.json> "
            "[--profile-dir <dir>] [--headless] [--validate] "
            "[--smoke '<command>' | --smoke-preset <name>] "
            "[--revoke-recipe <recipe.json> --revoke-key-id <id>] "
            "[--exposure-manifest <path>] [--dry-run] [--json]"
        )
        print()
        print("Aliases: banto sync quicktrust-api-key, banto sync quicktrust-webhook-secret")
        return

    forwarded = list(args)
    if "--allow-manual-cutover" not in forwarded:
        forwarded.append("--allow-manual-cutover")
    if "--profile-dir" not in forwarded:
        forwarded.extend([
            "--profile-dir",
            str(
                Path.home()
                / ".local"
                / "state"
                / "banto"
                / "browser-profiles"
                / "quicktrust"
            ),
        ])
    cmd_sync_browser_issue(forwarded)


def cmd_sync_cloudflare_account_token(args: list[str]) -> None:
    """Create a Cloudflare Account API token and propagate it."""
    name = None
    account_id = None
    policy_file = None
    token_name = None
    creator_token_env = DEFAULT_CLOUDFLARE_CREATOR_TOKEN_ENV
    creator_account = DEFAULT_CLOUDFLARE_CREATOR_ACCOUNT
    revoke_token_id = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync cloudflare-account-token <name> --account-id <id> "
                "--policy-file <policy.json> [--token-name <name>] "
                "[--creator-token-env CLOUDFLARE_TOKEN_CREATOR_API_TOKEN] "
                "[--creator-account cloudflare-token-creator] "
                "[--revoke-token <token_id>] [--validate] "
                "[--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
            )
            return
        if arg == "--account-id" and i + 1 < len(args):
            account_id = args[i + 1]
            i += 2
            continue
        if arg == "--policy-file" and i + 1 < len(args):
            policy_file = args[i + 1]
            i += 2
            continue
        if arg == "--token-name" and i + 1 < len(args):
            token_name = args[i + 1]
            i += 2
            continue
        if arg == "--creator-token-env" and i + 1 < len(args):
            creator_token_env = args[i + 1]
            i += 2
            continue
        if arg == "--creator-account" and i + 1 < len(args):
            creator_account = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-token" and i + 1 < len(args):
            revoke_token_id = args[i + 1]
            i += 2
            continue
        if arg in {"--config", "--smoke", "--smoke-preset"}:
            i += 2
            continue
        if arg in {"--json", "--dry-run", "--validate"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not account_id or not policy_file:
        print(
            "Usage: banto sync cloudflare-account-token <name> --account-id <id> "
            "--policy-file <policy.json> [--token-name <name>] "
            "[--creator-token-env CLOUDFLARE_TOKEN_CREATOR_API_TOKEN] "
            "[--creator-account cloudflare-token-creator] "
            "[--revoke-token <token_id>] [--validate] "
            "[--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    dry_run = "--dry-run" in args
    do_validate = "--validate" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)

    try:
        config, _ = _load_config(args)
        payload = load_cloudflare_token_policy(policy_file)
        plan = build_cloudflare_account_token_plan(
            config,
            name,
            account_id,
            payload,
            token_name=token_name,
        )
    except (CloudflareTokenRotatorError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        dry_payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.propagation_plan.secret_name,
            "env_name": plan.propagation_plan.env_name,
            "provider": plan.propagation_plan.provider,
            "rotation_class": plan.propagation_plan.rotation_class,
            "account_id": plan.account_id,
            "token_name": plan.token_name,
            "policy_count": len(plan.payload.get("policies", [])),
            "creator_resolution_order": [
                f"env:{creator_token_env}",
                f"keychain:{config.keychain_service}:{creator_account}",
            ],
            "revoke_token_id": revoke_token_id,
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
        }
        if _is_json(args):
            _json_out(dry_payload)
            return
        print(f"\nBANTO SYNC CLOUDFLARE ACCOUNT TOKEN — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:      {plan.propagation_plan.env_name}")
        print(f"  account_id:    {plan.account_id}")
        print(f"  token_name:    {plan.token_name}")
        print(f"  policies:      {len(plan.payload.get('policies', []))}")
        print(f"  validate:      {'yes' if do_validate else 'no'}")
        print(f"  smoke:         {smoke_label or '(none)'}")
        print("  creator_resolution:")
        print(f"    - env:{creator_token_env}")
        print(f"    - keychain:{config.keychain_service}:{creator_account}")
        print(f"  revoke_token:  {revoke_token_id or '(none)'}")
        print(f"  targets:       {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        return

    try:
        result = rotate_cloudflare_account_token(
            config,
            name,
            account_id,
            payload,
            token_name=token_name,
            creator_token_env=creator_token_env,
            creator_account=creator_account,
            revoke_token_id=revoke_token_id,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
        )
    except CloudflareTokenRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out(_cloudflare_rotation_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    if result.created is not None:
        print(f"Created Cloudflare token {result.created.token_id or '(no id)'} ({result.created.token_name})")
    if result.propagation is not None:
        if result.propagation.version is not None:
            print(f"Propagated '{result.propagation.plan.secret_name}' (now v{result.propagation.version})")
        if result.propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.propagation.sync_report)
    if result.cleanup_of_created_token is not None:
        cleanup = result.cleanup_of_created_token
        if cleanup.deleted:
            print(f"Cleaned up created Cloudflare token: {cleanup.token_id}")
        else:
            print(f"Cleanup failed: {cleanup.message}")
    if result.revoked_previous_token is not None:
        revoked = result.revoked_previous_token
        if revoked.deleted:
            print(f"Revoked previous Cloudflare token: {revoked.token_id}")
        else:
            print(f"Previous Cloudflare token revoke failed: {revoked.message}")
    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_stripe_webhook_endpoint(args: list[str]) -> None:
    """Create a Stripe webhook endpoint and propagate its signing secret."""
    name = None
    source_secret = None
    url = None
    events: list[str] = []
    description = None
    api_version = None
    connect = False
    delete_previous_endpoint_id = None
    use_stripe_cli_auth = False
    stripe_cli_live_mode = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(
                "Usage: banto sync stripe-webhook-endpoint <name> "
                "--source-secret <stripe-secret-name> --url <https://...> "
                "--event <event> [--event <event> ...] [--description <text>] "
                "[--api-version <version>] [--connect] "
                "[--delete-previous-endpoint <endpoint_id>] "
                "[--stripe-cli-auth [--live]] "
                "[--validate] [--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
            )
            return
        if arg == "--source-secret" and i + 1 < len(args):
            source_secret = args[i + 1]
            i += 2
            continue
        if arg == "--url" and i + 1 < len(args):
            url = args[i + 1]
            i += 2
            continue
        if arg == "--event" and i + 1 < len(args):
            events.append(args[i + 1])
            i += 2
            continue
        if arg == "--description" and i + 1 < len(args):
            description = args[i + 1]
            i += 2
            continue
        if arg == "--api-version" and i + 1 < len(args):
            api_version = args[i + 1]
            i += 2
            continue
        if arg == "--connect":
            connect = True
            i += 1
            continue
        if arg == "--delete-previous-endpoint" and i + 1 < len(args):
            delete_previous_endpoint_id = args[i + 1]
            i += 2
            continue
        if arg == "--stripe-cli-auth":
            use_stripe_cli_auth = True
            i += 1
            continue
        if arg == "--live":
            stripe_cli_live_mode = True
            i += 1
            continue
        if arg in {"--config", "--smoke", "--smoke-preset"}:
            i += 2
            continue
        if arg in {"--json", "--dry-run", "--validate"}:
            i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not source_secret or not url or not events:
        print(
            "Usage: banto sync stripe-webhook-endpoint <name> "
            "--source-secret <stripe-secret-name> --url <https://...> "
            "--event <event> [--event <event> ...] [--description <text>] "
            "[--api-version <version>] [--connect] "
            "[--delete-previous-endpoint <endpoint_id>] "
            "[--stripe-cli-auth [--live]] "
            "[--validate] [--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    dry_run = "--dry-run" in args
    do_validate = "--validate" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)
    if stripe_cli_live_mode and not use_stripe_cli_auth:
        print("Error: --live is only valid with --stripe-cli-auth.")
        sys.exit(1)

    try:
        config, _ = _load_config(args)
        plan = build_stripe_webhook_endpoint_plan(
            config,
            name,
            source_secret_name=source_secret,
            url=url,
            enabled_events=tuple(events),
            description=description,
            api_version=api_version,
            connect=connect,
        )
    except (StripeWebhookRotatorError, KeyError, ValueError) as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        dry_payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.propagation_plan.secret_name,
            "env_name": plan.propagation_plan.env_name,
            "source_secret_name": plan.source_secret_name,
            "url": plan.url,
            "enabled_events": list(plan.enabled_events),
            "connect": plan.connect,
            "delete_previous_endpoint_id": delete_previous_endpoint_id,
            "stripe_cli_auth": use_stripe_cli_auth,
            "stripe_cli_live_mode": stripe_cli_live_mode,
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
            "manual_cutover": True,
        }
        if _is_json(args):
            _json_out(dry_payload)
            return
        print(f"\nBANTO SYNC STRIPE WEBHOOK ENDPOINT — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:       {plan.propagation_plan.env_name}")
        print(f"  source_secret:  {plan.source_secret_name}")
        print(f"  url:            {plan.url}")
        print(f"  events:         {', '.join(plan.enabled_events)}")
        print(f"  connect:        {'yes' if plan.connect else 'no'}")
        print(f"  delete_previous:{delete_previous_endpoint_id or '(none)'}")
        print(f"  stripe_cli:     {'live' if stripe_cli_live_mode else 'test' if use_stripe_cli_auth else 'no'}")
        print(f"  validate:       {'yes' if do_validate else 'no'}")
        print(f"  smoke:          {smoke_label or '(none)'}")
        print(f"  targets:        {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        print("\n  manual_cutover: yes")
        print("  This creates a new webhook endpoint; runtime cutover must be coordinated.")
        return

    try:
        result = rotate_stripe_webhook_endpoint(
            config,
            name,
            source_secret_name=source_secret,
            url=url,
            enabled_events=tuple(events),
            description=description,
            api_version=api_version,
            connect=connect,
            delete_previous_endpoint_id=delete_previous_endpoint_id,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
            use_stripe_cli_auth=use_stripe_cli_auth,
            stripe_cli_live_mode=stripe_cli_live_mode,
        )
    except StripeWebhookRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out(_stripe_webhook_rotation_json_payload(result))
        if not result.ok:
            sys.exit(1)
        return

    if result.created is not None:
        print(f"Created Stripe webhook endpoint {result.created.endpoint_id}")
    if result.propagation is not None:
        if result.propagation.version is not None:
            print(f"Propagated '{result.propagation.plan.secret_name}' (now v{result.propagation.version})")
        if result.propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.propagation.sync_report)
    if result.cleanup_of_created_endpoint is not None:
        cleanup = result.cleanup_of_created_endpoint
        if cleanup.deleted:
            print(f"Cleaned up created Stripe webhook endpoint: {cleanup.endpoint_id}")
        else:
            print(f"Cleanup failed: {cleanup.message}")
    if result.deleted_previous_endpoint is not None:
        deleted = result.deleted_previous_endpoint
        if deleted.deleted:
            print(f"Deleted previous Stripe webhook endpoint: {deleted.endpoint_id}")
        else:
            print(f"Previous Stripe webhook endpoint delete failed: {deleted.message}")
    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_openai_service_account(args: list[str]) -> None:
    """Rotate an OpenAI secret via project service-account issuance."""
    config, _ = _load_config(args)

    name = None
    project_id = None
    service_account_name = None
    admin_key_env = DEFAULT_ADMIN_KEY_ENV
    admin_account = DEFAULT_ADMIN_ACCOUNT
    revoke_service_account_id = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--project-id" and i + 1 < len(args):
            project_id = args[i + 1]
            i += 2
            continue
        if arg == "--service-account-name" and i + 1 < len(args):
            service_account_name = args[i + 1]
            i += 2
            continue
        if arg == "--admin-key-env" and i + 1 < len(args):
            admin_key_env = args[i + 1]
            i += 2
            continue
        if arg == "--admin-account" and i + 1 < len(args):
            admin_account = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-service-account" and i + 1 < len(args):
            revoke_service_account_id = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not project_id:
        print(
            "Usage: banto sync openai-service-account <name> --project-id <proj_...> "
            "[--service-account-name <name>] [--admin-key-env OPENAI_ADMIN_KEY] "
            "[--admin-account openai-admin] [--revoke-service-account <svc_...>] "
            "[--validate] [--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    do_validate = "--validate" in args
    dry_run = "--dry-run" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)

    try:
        plan = build_openai_service_account_plan(
            config,
            name,
            project_id,
            service_account_name=service_account_name,
        )
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.propagation_plan.secret_name,
            "env_name": plan.propagation_plan.env_name,
            "provider": plan.propagation_plan.provider,
            "rotation_class": plan.propagation_plan.rotation_class,
            "implementation_phase": plan.propagation_plan.implementation_phase,
            "project_id": plan.project_id,
            "service_account_name": plan.service_account_name,
            "admin_resolution_order": [
                f"env:{admin_key_env}",
                f"keychain:{config.keychain_service}:{admin_account}",
            ],
            "revoke_service_account_id": revoke_service_account_id,
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
        }
        if _is_json(args):
            _json_out(payload)
            return

        print(f"\nBANTO SYNC OPENAI SERVICE ACCOUNT — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:              {plan.propagation_plan.env_name}")
        print(f"  project_id:            {plan.project_id}")
        print(f"  service_account_name:  {plan.service_account_name}")
        print(f"  validate:              {'yes' if do_validate else 'no'}")
        print(f"  smoke:                 {smoke_label or '(none)'}")
        print(f"  revoke_previous:       {revoke_service_account_id or '(none)'}")
        print("  admin_resolution:")
        print(f"    - env:{admin_key_env}")
        print(f"    - keychain:{config.keychain_service}:{admin_account}")
        print(f"  targets:               {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        return

    try:
        result = rotate_openai_service_account(
            config,
            name,
            project_id,
            service_account_name=service_account_name,
            admin_key_env=admin_key_env,
            admin_account=admin_account,
            revoke_service_account_id=revoke_service_account_id,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
        )
    except OpenAIRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out({
            "ok": result.ok,
            "name": result.plan.propagation_plan.secret_name,
            "env_name": result.plan.propagation_plan.env_name,
            "project_id": result.plan.project_id,
            "service_account_name": result.plan.service_account_name,
            "admin_key_source": result.admin_key_source,
            "created": None if result.created is None else {
                "service_account_id": result.created.service_account_id,
                "service_account_name": result.created.service_account_name,
                "api_key_id": result.created.api_key_id,
                "created_at": result.created.created_at,
            },
            "propagation": None if result.propagation is None else _propagation_json_payload(result.propagation),
            "rollback": None if result.rollback is None else {
                "attempted": result.rollback.attempted,
                "restored_previous_value": result.rollback.restored_previous_value,
                "version": result.rollback.version,
                "sync_ok": result.rollback.sync_ok,
                "message": result.rollback.message,
            },
            "cleanup_of_created_service_account": None
            if result.cleanup_of_created_service_account is None else {
                "service_account_id": result.cleanup_of_created_service_account.service_account_id,
                "deleted": result.cleanup_of_created_service_account.deleted,
                "message": result.cleanup_of_created_service_account.message,
            },
            "revoked_previous_service_account": None
            if result.revoked_previous_service_account is None else {
                "service_account_id": result.revoked_previous_service_account.service_account_id,
                "deleted": result.revoked_previous_service_account.deleted,
                "message": result.revoked_previous_service_account.message,
            },
            "error": result.error,
        })
        if not result.ok:
            sys.exit(1)
        return

    if result.created is not None:
        print(
            "Created OpenAI service account "
            f"{result.created.service_account_id} ({result.created.service_account_name})"
        )

    if result.propagation is not None:
        if result.propagation.validation is not None:
            if result.propagation.validation.status == "pass":
                print(f"Validation passed: {result.propagation.validation.message}")
            elif result.propagation.validation.status == "unknown":
                print(f"Validation unknown: {result.propagation.validation.message}")
            else:
                print(f"Validation failed: {result.propagation.validation.message}")

        if result.propagation.version is not None:
            print(
                f"Propagated '{result.propagation.plan.secret_name}' "
                f"(now v{result.propagation.version})"
            )
        if result.propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.propagation.sync_report)
        if result.propagation.smoke_check is not None:
            if result.propagation.smoke_check.success:
                print(f"Smoke test passed: {result.propagation.smoke_check.command}")
            else:
                print(f"Smoke test failed: {result.propagation.smoke_check.message}")

    if result.rollback is not None:
        if result.rollback.restored_previous_value:
            print(f"Rollback succeeded: {result.rollback.message}")
        else:
            print(f"Rollback failed: {result.rollback.message}")

    if result.cleanup_of_created_service_account is not None:
        cleanup = result.cleanup_of_created_service_account
        if cleanup.deleted:
            print(f"Cleaned up created service account: {cleanup.service_account_id}")
        else:
            print(f"Cleanup failed: {cleanup.message}")

    if result.revoked_previous_service_account is not None:
        revoked = result.revoked_previous_service_account
        if revoked.deleted:
            print(f"Revoked previous service account: {revoked.service_account_id}")
        else:
            print(f"Previous service account revoke failed: {revoked.message}")

    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_openai_service_accounts(args: list[str]) -> None:
    """List OpenAI project service accounts without exposing key values."""
    config, _ = _load_config(args)

    project_id = None
    admin_key_env = DEFAULT_ADMIN_KEY_ENV
    admin_account = DEFAULT_ADMIN_ACCOUNT
    limit = 20

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--project-id" and i + 1 < len(args):
            project_id = args[i + 1]
            i += 2
            continue
        if arg == "--admin-key-env" and i + 1 < len(args):
            admin_key_env = args[i + 1]
            i += 2
            continue
        if arg == "--admin-account" and i + 1 < len(args):
            admin_account = args[i + 1]
            i += 2
            continue
        if arg == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                print(f"Error: invalid --limit value: {args[i + 1]}")
                sys.exit(1)
            i += 2
            continue
        i += 1

    if not project_id:
        print(
            "Usage: banto sync openai-service-accounts --project-id <proj_...> "
            "[--limit N] [--admin-key-env OPENAI_ADMIN_KEY] "
            "[--admin-account openai-admin] [--json]"
        )
        sys.exit(1)

    try:
        admin_key, admin_key_source = resolve_openai_admin_key(
            config,
            env_var=admin_key_env,
            account=admin_account,
        )
        accounts = list_project_service_accounts(
            project_id,
            admin_key=admin_key,
            limit=limit,
        )
    except OpenAIRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    accounts = sorted(
        accounts,
        key=lambda item: item.created_at or 0,
        reverse=True,
    )

    if _is_json(args):
        _json_out({
            "ok": True,
            "project_id": project_id,
            "admin_key_source": admin_key_source,
            "count": len(accounts),
            "service_accounts": [
                {
                    "service_account_id": item.service_account_id,
                    "service_account_name": item.service_account_name,
                    "role": item.role,
                    "created_at": item.created_at,
                }
                for item in accounts
            ],
        })
        return

    print(f"\nBANTO SYNC OPENAI SERVICE ACCOUNTS — {project_id}\n")
    print(f"  admin_key_source:  {admin_key_source}")
    print(f"  count:             {len(accounts)}")
    for item in accounts:
        created_at = (
            datetime.fromtimestamp(item.created_at, tz=timezone.utc).isoformat()
            if item.created_at is not None else "unknown"
        )
        print(
            f"  - {item.service_account_id}  "
            f"{item.service_account_name}  "
            f"role={item.role or 'unknown'}  "
            f"created_at={created_at}"
        )


def cmd_sync_openai_revoke_service_account(args: list[str]) -> None:
    """Revoke one OpenAI project service account."""
    config, _ = _load_config(args)

    project_id = None
    service_account_id = None
    admin_key_env = DEFAULT_ADMIN_KEY_ENV
    admin_account = DEFAULT_ADMIN_ACCOUNT

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--project-id" and i + 1 < len(args):
            project_id = args[i + 1]
            i += 2
            continue
        if arg == "--service-account-id" and i + 1 < len(args):
            service_account_id = args[i + 1]
            i += 2
            continue
        if arg == "--admin-key-env" and i + 1 < len(args):
            admin_key_env = args[i + 1]
            i += 2
            continue
        if arg == "--admin-account" and i + 1 < len(args):
            admin_account = args[i + 1]
            i += 2
            continue
        i += 1

    if not project_id or not service_account_id:
        print(
            "Usage: banto sync openai-revoke-service-account "
            "--project-id <proj_...> --service-account-id <svc_...> "
            "[--admin-key-env OPENAI_ADMIN_KEY] [--admin-account openai-admin] "
            "[--json]"
        )
        sys.exit(1)

    try:
        admin_key, admin_key_source = resolve_openai_admin_key(
            config,
            env_var=admin_key_env,
            account=admin_account,
        )
        deleted = delete_project_service_account(
            project_id,
            service_account_id,
            admin_key=admin_key,
        )
    except OpenAIRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out({
            "ok": deleted.deleted,
            "project_id": project_id,
            "service_account_id": deleted.service_account_id,
            "deleted": deleted.deleted,
            "admin_key_source": admin_key_source,
        })
        if not deleted.deleted:
            sys.exit(1)
        return

    if deleted.deleted:
        print(
            "Revoked OpenAI service account "
            f"{deleted.service_account_id} from {project_id} "
            f"(admin_key_source={admin_key_source})"
        )
        return

    print(f"Error: Failed to revoke {service_account_id}")
    sys.exit(1)


def cmd_sync_google_api_key(args: list[str]) -> None:
    """Rotate a Google API key via the Google Cloud API Keys API."""
    config, _ = _load_config(args)

    name = None
    project_id = None
    display_name = None
    key_id = None
    quota_project = None
    access_token_env = DEFAULT_ACCESS_TOKEN_ENV
    adc_command = DEFAULT_ADC_COMMAND
    revoke_key_name = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--project-id" and i + 1 < len(args):
            project_id = args[i + 1]
            i += 2
            continue
        if arg == "--display-name" and i + 1 < len(args):
            display_name = args[i + 1]
            i += 2
            continue
        if arg == "--key-id" and i + 1 < len(args):
            key_id = args[i + 1]
            i += 2
            continue
        if arg == "--quota-project" and i + 1 < len(args):
            quota_project = args[i + 1]
            i += 2
            continue
        if arg == "--access-token-env" and i + 1 < len(args):
            access_token_env = args[i + 1]
            i += 2
            continue
        if arg == "--adc-command" and i + 1 < len(args):
            adc_command = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-key" and i + 1 < len(args):
            revoke_key_name = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not project_id:
        print(
            "Usage: banto sync google-api-key <name> --project-id <project> "
            "[--display-name <name>] [--key-id <key-id>] "
            "[--quota-project <project>] [--access-token-env GOOGLE_OAUTH_ACCESS_TOKEN] "
            "[--adc-command 'gcloud auth application-default print-access-token'] "
            "[--revoke-key <projects/.../locations/global/keys/...>] "
            "[--sync-shared-account-secrets] [--validate] "
            "[--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    do_validate = "--validate" in args
    dry_run = "--dry-run" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    sync_shared_account_secrets = "--sync-shared-account-secrets" in args
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)

    try:
        plan = build_google_api_key_plan(
            config,
            name,
            project_id,
            display_name=display_name,
            key_id=key_id,
            quota_project=quota_project,
        )
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.propagation_plan.secret_name,
            "env_name": plan.propagation_plan.env_name,
            "provider": plan.propagation_plan.provider,
            "rotation_class": plan.propagation_plan.rotation_class,
            "implementation_phase": plan.propagation_plan.implementation_phase,
            "project_id": plan.project_id,
            "parent": plan.parent,
            "display_name": plan.display_name,
            "key_id": plan.key_id,
            "quota_project": plan.quota_project,
            "access_token_resolution_order": [
                f"env:{access_token_env}",
                f"adc:{adc_command}",
                f"gcloud:{DEFAULT_GCLOUD_AUTH_COMMAND}",
            ],
            "revoke_key_name": revoke_key_name,
            "sync_shared_account_secrets": sync_shared_account_secrets,
            "shared_account_secret_names": list(plan.shared_account_secret_names),
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
        }
        if _is_json(args):
            _json_out(payload)
            return

        print(f"\nBANTO SYNC GOOGLE API KEY — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:                   {plan.propagation_plan.env_name}")
        print(f"  project_id:                 {plan.project_id}")
        print(f"  parent:                     {plan.parent}")
        print(f"  display_name:               {plan.display_name}")
        print(f"  key_id:                     {plan.key_id or '(auto)'}")
        print(f"  quota_project:              {plan.quota_project or '(none)'}")
        print(f"  validate:                   {'yes' if do_validate else 'no'}")
        print(f"  smoke:                      {smoke_label or '(none)'}")
        print(f"  revoke_previous_key:        {revoke_key_name or '(none)'}")
        print(f"  sync_shared_account_secrets:{'yes' if sync_shared_account_secrets else 'no'}")
        print("  access_token_resolution:")
        print(f"    - env:{access_token_env}")
        print(f"    - adc:{adc_command}")
        print(f"    - gcloud:{DEFAULT_GCLOUD_AUTH_COMMAND}")
        print(f"  targets:                    {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        if plan.shared_account_secret_names:
            print("  shared_account_secret_names:")
            for sibling in plan.shared_account_secret_names:
                print(f"    - {sibling}")
        return

    try:
        result = rotate_google_api_key(
            config,
            name,
            project_id,
            display_name=display_name,
            key_id=key_id,
            quota_project=quota_project,
            access_token_env=access_token_env,
            adc_command=adc_command,
            revoke_key_name=revoke_key_name,
            sync_shared_account_secrets=sync_shared_account_secrets,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
        )
    except GoogleRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out({
            "ok": result.ok,
            "name": result.plan.propagation_plan.secret_name,
            "env_name": result.plan.propagation_plan.env_name,
            "project_id": result.plan.project_id,
            "parent": result.plan.parent,
            "display_name": result.plan.display_name,
            "key_id": result.plan.key_id,
            "quota_project": result.plan.quota_project,
            "shared_account_secret_names": list(result.plan.shared_account_secret_names),
            "access_token_source": result.access_token_source,
            "created": None if result.created is None else {
                "key_name": result.created.key_name,
                "display_name": result.created.display_name,
                "key_id": result.created.key_id,
                "key_uid": result.created.key_uid,
                "operation_name": result.created.operation_name,
            },
            "primary_propagation": None if result.primary_propagation is None else _propagation_json_payload(result.primary_propagation),
            "sibling_propagations": [
                {
                    "secret_name": item.secret_name,
                    "attempted": item.attempted,
                    "ok": item.ok,
                    "skipped": item.skipped,
                    "reason": item.reason,
                    "propagation": None if item.propagation is None else _propagation_json_payload(item.propagation),
                }
                for item in result.sibling_propagations
            ],
            "rollback_entries": [
                {
                    "secret_name": item.secret_name,
                    "restored_previous_value": item.restored_previous_value,
                    "version": item.version,
                    "sync_ok": item.sync_ok,
                    "message": item.message,
                }
                for item in result.rollback_entries
            ],
            "cleanup_of_created_key": None if result.cleanup_of_created_key is None else {
                "key_name": result.cleanup_of_created_key.key_name,
                "deleted": result.cleanup_of_created_key.deleted,
                "operation_name": result.cleanup_of_created_key.operation_name,
                "message": result.cleanup_of_created_key.message,
            },
            "revoked_previous_key": None if result.revoked_previous_key is None else {
                "key_name": result.revoked_previous_key.key_name,
                "deleted": result.revoked_previous_key.deleted,
                "operation_name": result.revoked_previous_key.operation_name,
                "message": result.revoked_previous_key.message,
            },
            "error": result.error,
        })
        if not result.ok:
            sys.exit(1)
        return

    if result.created is not None:
        print(
            f"Created Google API key {result.created.key_name} "
            f"({result.created.display_name})"
        )

    if result.primary_propagation is not None:
        if result.primary_propagation.validation is not None:
            if result.primary_propagation.validation.status == "pass":
                print(f"Validation passed: {result.primary_propagation.validation.message}")
            elif result.primary_propagation.validation.status == "unknown":
                print(f"Validation unknown: {result.primary_propagation.validation.message}")
            else:
                print(f"Validation failed: {result.primary_propagation.validation.message}")
        if result.primary_propagation.version is not None:
            print(
                f"Propagated '{result.primary_propagation.plan.secret_name}' "
                f"(now v{result.primary_propagation.version})"
            )
        if result.primary_propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.primary_propagation.sync_report)
        if result.primary_propagation.smoke_check is not None:
            if result.primary_propagation.smoke_check.success:
                print(f"Smoke test passed: {result.primary_propagation.smoke_check.command}")
            else:
                print(f"Smoke test failed: {result.primary_propagation.smoke_check.message}")

    for sibling in result.sibling_propagations:
        if sibling.skipped:
            print(f"Skipped shared-account secret '{sibling.secret_name}': {sibling.reason}")
        elif sibling.ok:
            print(f"Synchronized shared-account secret '{sibling.secret_name}'")
        else:
            print(f"Shared-account sync failed for '{sibling.secret_name}'")

    for rollback in result.rollback_entries:
        if rollback.restored_previous_value:
            print(f"Rollback succeeded for '{rollback.secret_name}': {rollback.message}")
        else:
            print(f"Rollback failed for '{rollback.secret_name}': {rollback.message}")

    if result.cleanup_of_created_key is not None:
        cleanup = result.cleanup_of_created_key
        if cleanup.deleted:
            print(f"Cleaned up created Google API key: {cleanup.key_name}")
        else:
            print(f"Cleanup failed: {cleanup.message}")

    if result.revoked_previous_key is not None:
        revoked = result.revoked_previous_key
        if revoked.deleted:
            print(f"Revoked previous Google API key: {revoked.key_name}")
        else:
            print(f"Previous Google API key revoke failed: {revoked.message}")

    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
        sys.exit(1)


def cmd_sync_xai_api_key(args: list[str]) -> None:
    """Rotate an xAI API key via the xAI Management API."""
    config, _ = _load_config(args)

    name = None
    team_id = None
    key_name = None
    management_key_env = DEFAULT_XAI_MANAGEMENT_KEY_ENV
    management_account = DEFAULT_XAI_MANAGEMENT_ACCOUNT
    revoke_api_key_id = None
    acls: list[str] = []
    qps = None
    qpm = None
    tpm = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--team-id" and i + 1 < len(args):
            team_id = args[i + 1]
            i += 2
            continue
        if arg == "--key-name" and i + 1 < len(args):
            key_name = args[i + 1]
            i += 2
            continue
        if arg == "--management-key-env" and i + 1 < len(args):
            management_key_env = args[i + 1]
            i += 2
            continue
        if arg == "--management-account" and i + 1 < len(args):
            management_account = args[i + 1]
            i += 2
            continue
        if arg == "--revoke-api-key" and i + 1 < len(args):
            revoke_api_key_id = args[i + 1]
            i += 2
            continue
        if arg == "--acl" and i + 1 < len(args):
            acls.append(args[i + 1])
            i += 2
            continue
        if arg == "--qps" and i + 1 < len(args):
            qps = _parse_positive_int(args[i + 1], "--qps")
            i += 2
            continue
        if arg == "--qpm" and i + 1 < len(args):
            qpm = _parse_positive_int(args[i + 1], "--qpm")
            i += 2
            continue
        if arg == "--tpm" and i + 1 < len(args):
            tpm = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if name is None:
            name = arg
        i += 1

    if not name or not team_id:
        print(
            "Usage: banto sync xai-api-key <name> --team-id <team_id> "
            "[--key-name <name>] [--management-key-env XAI_MANAGEMENT_API_KEY] "
            "[--management-account xai-management] [--acl <acl> ...] "
            "[--qps N] [--qpm N] [--tpm N] "
            "[--wait-propagation] [--revoke-api-key <apiKeyId>] "
            "[--validate] [--smoke '<command>' | --smoke-preset <name>] [--dry-run]"
        )
        sys.exit(1)

    do_validate = "--validate" in args
    dry_run = "--dry-run" in args
    wait_for_propagation = "--wait-propagation" in args
    smoke_command, smoke_preset = _parse_smoke_options(args)
    smoke_label = _format_smoke_label(smoke_command, smoke_preset)

    try:
        plan = build_xai_api_key_plan(
            config,
            name,
            team_id,
            key_name=key_name,
            acls=tuple(acls) if acls else DEFAULT_XAI_ACLS,
            qps=qps,
            qpm=qpm,
            tpm=tpm,
        )
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "name": plan.propagation_plan.secret_name,
            "env_name": plan.propagation_plan.env_name,
            "provider": plan.propagation_plan.provider,
            "rotation_class": plan.propagation_plan.rotation_class,
            "implementation_phase": plan.propagation_plan.implementation_phase,
            "team_id": plan.team_id,
            "key_name": plan.key_name,
            "acls": list(plan.acls),
            "qps": plan.qps,
            "qpm": plan.qpm,
            "tpm": plan.tpm,
            "management_key_resolution_order": [
                f"env:{management_key_env}",
                f"keychain:{config.keychain_service}:{management_account}",
            ],
            "revoke_api_key_id": revoke_api_key_id,
            "wait_for_propagation": wait_for_propagation,
            "validate": do_validate,
            "smoke": smoke_label,
            "smoke_preset": smoke_preset,
            "targets": list(plan.propagation_plan.targets),
        }
        if _is_json(args):
            _json_out(payload)
            return

        print(f"\nBANTO SYNC XAI API KEY — Dry run for {plan.propagation_plan.secret_name}\n")
        print(f"  env_name:              {plan.propagation_plan.env_name}")
        print(f"  team_id:               {plan.team_id}")
        print(f"  key_name:              {plan.key_name}")
        print(f"  acls:                  {', '.join(plan.acls)}")
        print(f"  qps:                   {plan.qps or '(none)'}")
        print(f"  qpm:                   {plan.qpm or '(none)'}")
        print(f"  tpm:                   {plan.tpm or '(none)'}")
        print(f"  wait_propagation:      {'yes' if wait_for_propagation else 'no'}")
        print(f"  validate:              {'yes' if do_validate else 'no'}")
        print(f"  smoke:                 {smoke_label or '(none)'}")
        print(f"  revoke_previous_key:   {revoke_api_key_id or '(none)'}")
        print("  management_resolution:")
        print(f"    - env:{management_key_env}")
        print(f"    - keychain:{config.keychain_service}:{management_account}")
        print(f"  targets:               {len(plan.propagation_plan.targets)}")
        for label in plan.propagation_plan.targets:
            print(f"    - {label}")
        return

    try:
        result = rotate_xai_api_key(
            config,
            name,
            team_id,
            key_name=key_name,
            acls=tuple(acls) if acls else DEFAULT_XAI_ACLS,
            qps=qps,
            qpm=qpm,
            tpm=tpm,
            management_key_env=management_key_env,
            management_account=management_account,
            revoke_api_key_id=revoke_api_key_id,
            wait_for_propagation=wait_for_propagation,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
        )
    except XAIRotatorError as exc:
        if _is_json(args):
            _json_out({"ok": False, "error": str(exc)})
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if _is_json(args):
        _json_out({
            "ok": result.ok,
            "name": result.plan.propagation_plan.secret_name,
            "env_name": result.plan.propagation_plan.env_name,
            "team_id": result.plan.team_id,
            "key_name": result.plan.key_name,
            "acls": list(result.plan.acls),
            "management_key_source": result.management_key_source,
            "created": None if result.created is None else {
                "api_key_id": result.created.api_key_id,
                "name": result.created.name,
                "redacted_api_key": result.created.redacted_api_key,
                "create_time": result.created.create_time,
            },
            "propagation_status": None if result.propagation_status is None else {
                "api_key_id": result.propagation_status.api_key_id,
                "propagated": result.propagation_status.propagated,
                "clusters": result.propagation_status.clusters,
                "message": result.propagation_status.message,
            },
            "propagation": None if result.propagation is None else _propagation_json_payload(result.propagation),
            "rollback": None if result.rollback is None else {
                "attempted": result.rollback.attempted,
                "restored_previous_value": result.rollback.restored_previous_value,
                "version": result.rollback.version,
                "sync_ok": result.rollback.sync_ok,
                "message": result.rollback.message,
            },
            "cleanup_of_created_key": None if result.cleanup_of_created_key is None else {
                "api_key_id": result.cleanup_of_created_key.api_key_id,
                "deleted": result.cleanup_of_created_key.deleted,
                "message": result.cleanup_of_created_key.message,
            },
            "revoked_previous_key": None if result.revoked_previous_key is None else {
                "api_key_id": result.revoked_previous_key.api_key_id,
                "deleted": result.revoked_previous_key.deleted,
                "message": result.revoked_previous_key.message,
            },
            "error": result.error,
        })
        if not result.ok:
            sys.exit(1)
        return

    if result.created is not None:
        print(f"Created xAI API key {result.created.api_key_id} ({result.created.name})")

    if result.propagation_status is not None:
        if result.propagation_status.propagated:
            print("xAI propagation check passed.")
        else:
            print(f"xAI propagation check failed: {result.propagation_status.message}")

    if result.propagation is not None:
        if result.propagation.validation is not None:
            if result.propagation.validation.status == "pass":
                print(f"Validation passed: {result.propagation.validation.message}")
            elif result.propagation.validation.status == "unknown":
                print(f"Validation unknown: {result.propagation.validation.message}")
            else:
                print(f"Validation failed: {result.propagation.validation.message}")
        if result.propagation.version is not None:
            print(
                f"Propagated '{result.propagation.plan.secret_name}' "
                f"(now v{result.propagation.version})"
            )
        if result.propagation.sync_report is not None:
            print("Syncing to all targets...")
            _print_report(result.propagation.sync_report)
        if result.propagation.smoke_check is not None:
            if result.propagation.smoke_check.success:
                print(f"Smoke test passed: {result.propagation.smoke_check.command}")
            else:
                print(f"Smoke test failed: {result.propagation.smoke_check.message}")

    if result.rollback is not None:
        if result.rollback.restored_previous_value:
            print(f"Rollback succeeded: {result.rollback.message}")
        else:
            print(f"Rollback failed: {result.rollback.message}")

    if result.cleanup_of_created_key is not None:
        cleanup = result.cleanup_of_created_key
        if cleanup.deleted:
            print(f"Cleaned up created xAI API key: {cleanup.api_key_id}")
        else:
            print(f"Cleanup failed: {cleanup.message}")

    if result.revoked_previous_key is not None:
        revoked = result.revoked_previous_key
        if revoked.deleted:
            print(f"Revoked previous xAI API key: {revoked.api_key_id}")
        else:
            print(f"Previous xAI API key revoke failed: {revoked.message}")

    if not result.ok:
        if result.error:
            print(f"Error: {result.error}")
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
    If names are provided, validates only those sync-managed secrets.
    With --keychain flag, scans Keychain directly for known provider patterns.
    With --dry-run, shows which keys would be tested without sending them.
    """
    from .validate import validate_key, list_supported_providers, SERVICE_PATTERNS, should_exclude

    config, _ = _load_config(args)
    scan_keychain = "--keychain" in args
    dry_run = "--dry-run" in args
    requested_names: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--config":
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        requested_names.append(arg)
        i += 1

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
                if requested_names and svc not in requested_names:
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
        entries: list[tuple[str, SecretEntry]] = []
        if requested_names:
            missing = [name for name in requested_names if config.get_secret(name) is None]
            if missing:
                print(f"Error: Secret(s) not found: {', '.join(missing)}")
                sys.exit(1)
            entries = [
                (name, config.get_secret(name))
                for name in requested_names
                if config.get_secret(name) is not None
            ]
        else:
            entries = list(config.secrets.items())
        for name, entry in entries:
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
    "classify": cmd_sync_classify,
    "incident-report": cmd_sync_incident_report,
    "vercel-inventory": cmd_sync_vercel_inventory,
    "push": cmd_sync_push,
    "add": cmd_sync_add,
    "import-keychain": cmd_sync_import_keychain,
    "google-api-key": cmd_sync_google_api_key,
    "openai-service-account": cmd_sync_openai_service_account,
    "openai-service-accounts": cmd_sync_openai_service_accounts,
    "openai-revoke-service-account": cmd_sync_openai_revoke_service_account,
    "xai-api-key": cmd_sync_xai_api_key,
    "browser-batch": cmd_sync_browser_batch,
    "browser-record": cmd_sync_browser_record,
    "browser-issue": cmd_sync_browser_issue,
    "browser-revoke": cmd_sync_browser_revoke,
    "quicktrust-credential": cmd_sync_quicktrust_credential,
    "quicktrust-api-key": cmd_sync_quicktrust_credential,
    "quicktrust-webhook-secret": cmd_sync_quicktrust_credential,
    "cloudflare-account-token": cmd_sync_cloudflare_account_token,
    "stripe-webhook-endpoint": cmd_sync_stripe_webhook_endpoint,
    "propagate": cmd_sync_propagate,
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
        print("  classify            Group secrets by rotation strategy")
        print("  incident-report     Prioritize secrets for incident response")
        print("  vercel-inventory    Read-only Vercel env inventory without values")
        print("  validate            Test API keys against provider endpoints")
        print("  push [--validate]   Sync secrets to targets (--validate first)")
        print("  add <name> ...      Add a new secret")
        print("  import-keychain <name> --from-service <service>")
        print("  google-api-key <name> --project-id <project>")
        print("  openai-service-account <name> --project-id <proj_...>")
        print("  openai-service-accounts --project-id <proj_...>")
        print("  openai-revoke-service-account --project-id <proj_...> --service-account-id <svc_...>")
        print("  xai-api-key <name> --team-id <team_id>")
        print("  browser-batch <plan.json>")
        print("  browser-record <name> --start-url <url> --output <recipe.json>")
        print("  browser-issue <name> --recipe <recipe.json>")
        print("  browser-revoke <name> --recipe <recipe.json> --key-id <id>")
        print("  quicktrust-credential <name> --recipe <recipe.json>")
        print("  cloudflare-account-token <name> --account-id <id> --policy-file <json>")
        print("  stripe-webhook-endpoint <name> --source-secret <secret> --url <https://...> --event <event>")
        print("  propagate <name>    Store + sync replacement value via common flow")
        print("  rotate <name>       Rotate a secret (update + re-sync)")
        print("  audit [--max-age-days N]  Check drift + fingerprint + stale")
        print("  history <name>      Show version history")
        print("  run [--env E] -- <cmd>  Run command with secrets as env vars")
        print("  export [--format]   Export secrets (env/json/docker)")
        print("  import <file>       Import from .env or .json file")
        print("  ui [--port N]       Launch local web UI")
        print("\nSmoke presets:")
        for preset in list_smoke_presets():
            print(f"  {preset.name:<18} {preset.description}")
        sys.exit(0)

    sub = args[0]
    if sub not in SYNC_COMMANDS:
        print(f"Unknown sync command: {sub}", file=sys.stderr)
        print(f"Available: {', '.join(SYNC_COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    SYNC_COMMANDS[sub](args[1:])

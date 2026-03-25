# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Sync orchestration — Keychain to platform targets.

Reads secret values from Keychain and deploys them to each configured
target via platform drivers. Values are held only in memory during sync
and never logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..keychain import KeychainStore, KeyNotFoundError
from . import audit
from .config import SecretEntry, SyncConfig, Target
from .drivers import get_driver
from .notifiers.base import EventPayload, SyncEvent
from .sync_state import SyncState


@dataclass
class SyncResult:
    """Result of a sync operation for one secret+target pair."""

    secret_name: str
    target_label: str
    success: bool
    message: str = ""


@dataclass
class SyncReport:
    """Aggregate report for a sync run."""

    results: list[SyncResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def all_ok(self) -> bool:
        return self.fail_count == 0


def fire_notifications(config: SyncConfig, event: SyncEvent, report: SyncReport,
                       secret_name: str = "*") -> None:
    """Send notifications to all configured notifiers for the given event."""
    from .notifiers import get_notifier

    for nc in config.notifiers:
        if event.value not in nc.events:
            continue
        try:
            notifier = get_notifier(nc.name, nc.webhook_url)
            targets = [r.target_label for r in report.results]
            payload = EventPayload(
                event=event,
                secret_name=secret_name,
                targets=targets,
                ok_count=report.ok_count,
                fail_count=report.fail_count,
            )
            notifier.notify(payload)
        except (ValueError, Exception):
            pass  # Notification failure should never block sync operations


def _sync_one_target(
    entry: SecretEntry,
    target: Target,
    value: str,
    *,
    audit_log: Path | None = None,
) -> SyncResult:
    """Sync a single secret to a single target."""
    label = target.label
    try:
        driver = get_driver(target.platform)

        project = target.file if target.platform == "local" else target.project
        ok = driver.put(entry.env_name, value, project)

        result_str = "OK" if ok else "FAIL"
        audit.log_event("SYNC", entry.name, label, result_str, log_path=audit_log)
        return SyncResult(
            secret_name=entry.name,
            target_label=label,
            success=ok,
            message="" if ok else "Driver put returned False",
        )
    except FileNotFoundError as e:
        audit.log_event("SYNC", entry.name, label, "CLI_NOT_FOUND", log_path=audit_log)
        return SyncResult(
            secret_name=entry.name,
            target_label=label,
            success=False,
            message=str(e),
        )
    except Exception as e:
        audit.log_event("SYNC", entry.name, label, "ERROR", log_path=audit_log)
        return SyncResult(
            secret_name=entry.name,
            target_label=label,
            success=False,
            message=str(type(e).__name__),
        )


def sync_secret(
    config: SyncConfig,
    secret_name: str,
    *,
    audit_log: Path | None = None,
) -> SyncReport:
    """Sync one secret from Keychain to all its configured targets."""
    report = SyncReport()
    entry = config.get_secret(secret_name)
    if entry is None:
        report.results.append(SyncResult(
            secret_name=secret_name,
            target_label="(config)",
            success=False,
            message=f"Secret '{secret_name}' not found in sync config",
        ))
        return report

    kc = KeychainStore(service_prefix=config.keychain_service)
    value = kc.get(entry.account)
    if value is None:
        # Fallback: try raw service name (for --account references like "claude-mcp-openai")
        import subprocess as _sp
        _raw = _sp.run(
            ["security", "find-generic-password", "-s", entry.account, "-w"],
            capture_output=True, text=True,
        )
        if _raw.returncode == 0 and _raw.stdout.strip():
            value = _raw.stdout.strip()
    if value is None:
        report.results.append(SyncResult(
            secret_name=secret_name,
            target_label="keychain",
            success=False,
            message=f"Secret not found in Keychain: account={entry.account}",
        ))
        return report

    for target in entry.targets:
        result = _sync_one_target(entry, target, value, audit_log=audit_log)
        report.results.append(result)

    # Record sync fingerprint for drift detection
    if report.ok_count > 0:
        state = SyncState()
        synced_targets = [r.target_label for r in report.results if r.success]
        state.record_push(secret_name, value, synced_targets)

    event = SyncEvent.SYNC_OK if report.all_ok else SyncEvent.SYNC_FAIL
    fire_notifications(config, event, report, secret_name=secret_name)

    return report


def sync_all(
    config: SyncConfig,
    *,
    audit_log: Path | None = None,
) -> SyncReport:
    """Sync all secrets from Keychain to their configured targets."""
    report = SyncReport()
    for name in config.secrets:
        sub = sync_secret(config, name, audit_log=audit_log)
        report.results.extend(sub.results)

    event = SyncEvent.SYNC_OK if report.all_ok else SyncEvent.SYNC_FAIL
    fire_notifications(config, event, report, secret_name="*")

    return report


@dataclass
class StatusEntry:
    """Status of one secret across all its targets."""

    secret_name: str
    env_name: str
    keychain_exists: bool
    target_status: dict[str, bool | None] = field(default_factory=dict)
    # None = not a target for this secret, True = exists, False = missing


def check_status(config: SyncConfig) -> list[StatusEntry]:
    """Check existence of all secrets across Keychain and targets."""
    kc = KeychainStore(service_prefix=config.keychain_service)
    entries: list[StatusEntry] = []
    for name, secret in config.secrets.items():
        kc_exists = kc.exists(secret.account)
        target_status: dict[str, bool | None] = {}
        for target in secret.targets:
            try:
                driver = get_driver(target.platform)
                project = target.file if target.platform == "local" else target.project
                target_status[target.label] = driver.exists(secret.env_name, project)
            except Exception:
                target_status[target.label] = False
        entries.append(StatusEntry(
            secret_name=name,
            env_name=secret.env_name,
            keychain_exists=kc_exists,
            target_status=target_status,
        ))
    return entries


def remove_secret(
    config: SyncConfig,
    secret_name: str,
    *,
    audit_log: Path | None = None,
) -> SyncReport:
    """Remove a secret from Keychain and all its targets."""
    report = SyncReport()
    entry = config.get_secret(secret_name)
    if entry is None:
        report.results.append(SyncResult(
            secret_name=secret_name,
            target_label="(config)",
            success=False,
            message=f"Secret '{secret_name}' not found in sync config",
        ))
        return report

    # Delete from Keychain
    kc = KeychainStore(service_prefix=config.keychain_service)
    kc_ok = kc.delete(entry.account)
    audit.log_event(
        "REMOVE", entry.name, "keychain",
        "OK" if kc_ok else "NOT_FOUND",
        log_path=audit_log,
    )

    # Delete from all targets
    for target in entry.targets:
        label = target.label
        try:
            driver = get_driver(target.platform)
            project = target.file if target.platform == "local" else target.project
            ok = driver.delete(entry.env_name, project)
            result_str = "OK" if ok else "NOT_FOUND"
            audit.log_event("REMOVE", entry.name, label, result_str, log_path=audit_log)
            report.results.append(SyncResult(
                secret_name=entry.name, target_label=label,
                success=True, message=result_str,
            ))
        except Exception as e:
            audit.log_event("REMOVE", entry.name, label, "ERROR", log_path=audit_log)
            report.results.append(SyncResult(
                secret_name=entry.name, target_label=label,
                success=False, message=str(type(e).__name__),
            ))

    # Remove from config
    config.remove_secret(secret_name)

    return report

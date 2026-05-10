# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Common propagation flow for sync-managed secrets.

This module powers the phase-2 `propagate_only` workflow:
receive a replacement value, optionally validate it, store it in Keychain,
sync it to configured targets, and optionally run a post-sync smoke command.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass

from ..keychain import KeychainStore
from .capabilities import SecretClassification, classify_secret
from .config import SecretEntry, SyncConfig
from .history import HistoryStore
from .smoke_presets import run_smoke_preset, smoke_preset_exists
from .sync import SyncReport, sync_secret
from .validate import ValidationResult, validate_key

ALLOWED_PROPAGATION_CLASSES = {"full_auto", "partial_auto", "propagate_only"}


@dataclass(frozen=True)
class PropagationPlan:
    """Classification plus target coverage for one secret."""

    secret_name: str
    env_name: str
    account: str
    provider: str
    rotation_class: str
    implementation_phase: str
    matched_rule: str
    notes: str
    targets: tuple[str, ...]

    @property
    def is_allowed(self) -> bool:
        return self.rotation_class in ALLOWED_PROPAGATION_CLASSES


@dataclass(frozen=True)
class SmokeCheckResult:
    """Result of an optional post-sync smoke command."""

    command: str
    success: bool
    exit_code: int
    message: str = ""


@dataclass(frozen=True)
class PropagationResult:
    """Result of the end-to-end propagation flow."""

    plan: PropagationPlan
    stored: bool
    version: int | None
    sync_report: SyncReport | None
    validation: ValidationResult | None = None
    smoke_check: SmokeCheckResult | None = None

    @property
    def ok(self) -> bool:
        sync_ok = self.sync_report.all_ok if self.sync_report is not None else True
        validation_ok = self.validation is None or self.validation.status != "fail"
        smoke_ok = self.smoke_check is None or self.smoke_check.success
        return self.stored and validation_ok and sync_ok and smoke_ok


def build_propagation_plan(config: SyncConfig, secret_name: str) -> PropagationPlan:
    """Build a propagation plan for a single configured secret."""
    entry = config.get_secret(secret_name)
    if entry is None:
        raise KeyError(secret_name)

    classification = classify_secret(secret_name, entry.env_name)
    return _plan_from_entry(entry, classification)


def _plan_from_entry(entry: SecretEntry, classification: SecretClassification) -> PropagationPlan:
    return PropagationPlan(
        secret_name=entry.name,
        env_name=entry.env_name,
        account=entry.account,
        provider=classification.provider,
        rotation_class=classification.rotation_class,
        implementation_phase=classification.implementation_phase,
        matched_rule=classification.matched_rule,
        notes=classification.notes,
        targets=tuple(target.label for target in entry.targets),
    )


def validate_propagation_plan(plan: PropagationPlan) -> None:
    """Raise ValueError if a plan is not eligible for the common propagation flow."""
    if plan.is_allowed:
        return

    if plan.rotation_class == "inventory_only":
        raise ValueError(
            f"{plan.env_name} is inventory_only; track coverage but do not rotate via propagate."
        )
    if plan.rotation_class == "manual_cutover":
        raise ValueError(
            f"{plan.env_name} is manual_cutover; use a dedicated runbook instead of blind overwrite."
        )
    if plan.rotation_class == "review_required":
        raise ValueError(
            f"{plan.env_name} is review_required; classify it before automating."
        )
    raise ValueError(f"{plan.env_name} cannot use propagate flow ({plan.rotation_class}).")


def validate_new_value(plan: PropagationPlan, value: str) -> ValidationResult:
    """Validate a replacement secret value with the provider's lightweight validator."""
    return validate_key(plan.provider, value)


def run_smoke_command(command: str, *, env_name: str, value: str, timeout: int = 60) -> SmokeCheckResult:
    """Run an allowlisted-style smoke command with the new value injected into the env."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return SmokeCheckResult(command=command, success=False, exit_code=2, message=str(exc))

    if not argv:
        return SmokeCheckResult(command=command, success=False, exit_code=2, message="empty command")

    env = os.environ.copy()
    env[env_name] = value
    try:
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return SmokeCheckResult(
            command=command,
            success=False,
            exit_code=127,
            message=f"Command not found: {argv[0]}",
        )
    except subprocess.TimeoutExpired:
        return SmokeCheckResult(
            command=command,
            success=False,
            exit_code=124,
            message=f"Command timed out ({timeout}s)",
        )

    message = "" if result.returncode == 0 else f"Command failed (exit {result.returncode})"
    return SmokeCheckResult(
        command=command,
        success=result.returncode == 0,
        exit_code=result.returncode,
        message=message,
    )


def run_smoke_preset_check(
    plan: PropagationPlan,
    preset_name: str,
    *,
    value: str,
) -> SmokeCheckResult:
    """Run an allowlisted built-in smoke preset."""
    if not smoke_preset_exists(preset_name):
        return SmokeCheckResult(
            command=f"preset:{preset_name}",
            success=False,
            exit_code=2,
            message=f"Unknown smoke preset: {preset_name}",
        )

    success, message = run_smoke_preset(
        preset_name,
        classification=classify_secret(plan.secret_name, plan.env_name),
        value=value,
    )
    return SmokeCheckResult(
        command=f"preset:{preset_name}",
        success=success,
        exit_code=0 if success else 1,
        message=message,
    )


def propagate_secret(
    config: SyncConfig,
    secret_name: str,
    value: str,
    *,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
    allow_manual_cutover: bool = False,
) -> PropagationResult:
    """Store a replacement value, sync it to targets, and optionally validate/smoke-test."""
    plan = build_propagation_plan(config, secret_name)
    if not (allow_manual_cutover and plan.rotation_class == "manual_cutover"):
        validate_propagation_plan(plan)

    if smoke_command and smoke_preset:
        return PropagationResult(
            plan=plan,
            stored=False,
            version=None,
            sync_report=None,
            smoke_check=SmokeCheckResult(
                command="smoke",
                success=False,
                exit_code=2,
                message="Specify either smoke_command or smoke_preset, not both.",
            ),
        )

    validation = validate_new_value(plan, value) if do_validate else None
    if validation is not None and validation.status == "fail":
        return PropagationResult(
            plan=plan,
            stored=False,
            version=None,
            sync_report=None,
            validation=validation,
        )

    kc = KeychainStore(service_prefix=config.keychain_service)
    if not kc.store(plan.account, value):
        return PropagationResult(
            plan=plan,
            stored=False,
            version=None,
            sync_report=None,
            validation=validation,
        )

    history = HistoryStore()
    new_ver = history.record(secret_name, value, config.keychain_service)
    if new_ver is None:
        return PropagationResult(
            plan=plan,
            stored=False,
            version=None,
            sync_report=None,
            validation=validation,
        )

    sync_report = sync_secret(config, secret_name)
    smoke_check = None
    if smoke_command:
        smoke_check = run_smoke_command(smoke_command, env_name=plan.env_name, value=value)
    elif smoke_preset:
        smoke_check = run_smoke_preset_check(plan, smoke_preset, value=value)

    return PropagationResult(
        plan=plan,
        stored=True,
        version=new_ver.version,
        sync_report=sync_report,
        validation=validation,
        smoke_check=smoke_check,
    )

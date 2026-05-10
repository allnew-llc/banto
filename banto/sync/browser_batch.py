# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Batch orchestration for closed-loop browser key rotation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser_issuer import (
    BrowserIssueResult,
    BrowserIssuerError,
    build_browser_issue_plan,
    build_browser_retirement_plan,
    issue_secret_with_browser,
    load_browser_issuer_recipe,
    load_browser_retirement_recipe,
)
from .config import SyncConfig

SUPPORTED_BROWSER_BATCH_VERSION = 1


@dataclass(frozen=True)
class BrowserBatchItem:
    """One closed-loop browser rotation item."""

    name: str
    recipe_path: Path
    profile_dir: Path | None = None
    headless: bool = False
    do_validate: bool = False
    smoke_command: str | None = None
    smoke_preset: str | None = None
    exposure_manifest_path: Path | None = None
    revoke_recipe_path: Path | None = None
    revoke_key_id: str | None = None
    revoke_key_label: str | None = None
    revoke_profile_dir: Path | None = None
    revoke_headless: bool | None = None
    enabled: bool = True


@dataclass(frozen=True)
class BrowserBatchPlan:
    """Parsed browser batch plan."""

    name: str
    items: tuple[BrowserBatchItem, ...]
    fail_fast: bool = True


@dataclass(frozen=True)
class BrowserBatchItemOutcome:
    """Outcome for one batch item without secret values."""

    name: str
    ok: bool
    skipped: bool
    dry_run: bool
    provider: str | None = None
    recipe_name: str | None = None
    targets: tuple[str, ...] = ()
    retirement_planned: bool = False
    revoke_key_id: str | None = None
    result: BrowserIssueResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class BrowserBatchResult:
    """End-to-end batch result without secret values."""

    plan: BrowserBatchPlan
    outcomes: tuple[BrowserBatchItemOutcome, ...]
    dry_run: bool

    @property
    def ok(self) -> bool:
        return all(outcome.ok or outcome.skipped for outcome in self.outcomes)


def load_browser_batch_plan(path: Path | str) -> BrowserBatchPlan:
    """Load a browser batch plan from JSON."""
    plan_path = Path(path)
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserIssuerError(f"Failed to load browser batch plan: {exc}") from exc
    if not isinstance(raw, dict):
        raise BrowserIssuerError("Browser batch plan must be a JSON object.")
    return browser_batch_plan_from_dict(raw, base_dir=plan_path.parent)


def browser_batch_plan_from_dict(raw: dict[str, Any], *, base_dir: Path | None = None) -> BrowserBatchPlan:
    """Validate a browser batch plan dictionary."""
    version = raw.get("version")
    if version != SUPPORTED_BROWSER_BATCH_VERSION:
        raise BrowserIssuerError(
            f"Unsupported browser batch version: {version!r}. "
            f"Expected {SUPPORTED_BROWSER_BATCH_VERSION}."
        )
    defaults = raw.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise BrowserIssuerError("browser batch defaults must be an object.")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise BrowserIssuerError("browser batch requires a non-empty items array.")
    base = base_dir or Path.cwd()
    items = tuple(_parse_batch_item(item, defaults, base) for item in items_raw)
    return BrowserBatchPlan(
        name=_optional_str(raw.get("name")) or "browser-batch",
        items=items,
        fail_fast=bool(raw.get("fail_fast", True)),
    )


def run_browser_batch(
    config: SyncConfig,
    plan: BrowserBatchPlan,
    *,
    dry_run: bool = False,
) -> BrowserBatchResult:
    """Run or validate a browser batch plan."""
    outcomes: list[BrowserBatchItemOutcome] = []
    for item in plan.items:
        if not item.enabled:
            outcomes.append(BrowserBatchItemOutcome(
                name=item.name,
                ok=True,
                skipped=True,
                dry_run=dry_run,
            ))
            continue
        outcome = _run_batch_item(config, item, dry_run=dry_run)
        outcomes.append(outcome)
        if plan.fail_fast and not outcome.ok:
            break
    return BrowserBatchResult(
        plan=plan,
        outcomes=tuple(outcomes),
        dry_run=dry_run,
    )


def _run_batch_item(
    config: SyncConfig,
    item: BrowserBatchItem,
    *,
    dry_run: bool,
) -> BrowserBatchItemOutcome:
    try:
        recipe = load_browser_issuer_recipe(item.recipe_path)
        issue_plan = build_browser_issue_plan(
            config,
            item.name,
            recipe,
            profile_dir=item.profile_dir,
            headless=item.headless,
        )
        retirement = _resolve_batch_retirement(item)
        retire_recipe = None
        if retirement["recipe_path"]:
            retire_recipe = load_browser_retirement_recipe(retirement["recipe_path"])
            build_browser_retirement_plan(
                config,
                item.name,
                retire_recipe,
                key_id=retirement["key_id"] or "",
                key_label=retirement["key_label"],
                profile_dir=item.revoke_profile_dir,
                headless=item.headless if item.revoke_headless is None else item.revoke_headless,
            )
        elif retirement["key_id"]:
            raise BrowserIssuerError(
                f"{item.name}: revoke key id requires a revoke recipe or exposure manifest."
            )

        if dry_run:
            return BrowserBatchItemOutcome(
                name=item.name,
                ok=True,
                skipped=False,
                dry_run=True,
                provider=issue_plan.propagation_plan.provider,
                recipe_name=recipe.name,
                targets=issue_plan.propagation_plan.targets,
                retirement_planned=retire_recipe is not None,
                revoke_key_id=retirement["key_id"],
            )

        result = issue_secret_with_browser(
            config,
            item.name,
            recipe,
            profile_dir=item.profile_dir,
            headless=item.headless,
            do_validate=item.do_validate,
            smoke_command=item.smoke_command,
            smoke_preset=item.smoke_preset,
            retire_recipe=retire_recipe,
            retire_key_id=retirement["key_id"],
            retire_key_label=retirement["key_label"],
            retire_profile_dir=item.revoke_profile_dir,
            retire_headless=item.headless if item.revoke_headless is None else item.revoke_headless,
        )
        return BrowserBatchItemOutcome(
            name=item.name,
            ok=result.ok,
            skipped=False,
            dry_run=False,
            provider=result.plan.propagation_plan.provider,
            recipe_name=result.plan.recipe.name,
            targets=result.plan.propagation_plan.targets,
            retirement_planned=result.retirement is not None,
            revoke_key_id=retirement["key_id"],
            result=result,
            error=result.error,
        )
    except (BrowserIssuerError, KeyError, ValueError) as exc:
        return BrowserBatchItemOutcome(
            name=item.name,
            ok=False,
            skipped=False,
            dry_run=dry_run,
            error=str(exc),
        )


def _parse_batch_item(raw: Any, defaults: dict[str, Any], base_dir: Path) -> BrowserBatchItem:
    if not isinstance(raw, dict):
        raise BrowserIssuerError("browser batch item must be an object.")
    name = _required_str(raw, "name")
    recipe_path = _resolve_path(_get_with_default(raw, defaults, "recipe"), base_dir, required=True)
    return BrowserBatchItem(
        name=name,
        recipe_path=recipe_path,
        profile_dir=_resolve_path(_get_with_default(raw, defaults, "profile_dir"), base_dir),
        headless=bool(_get_with_default(raw, defaults, "headless", False)),
        do_validate=bool(_get_with_default(raw, defaults, "validate", False)),
        smoke_command=_optional_str(_get_with_default(raw, defaults, "smoke")),
        smoke_preset=_optional_str(_get_with_default(raw, defaults, "smoke_preset")),
        exposure_manifest_path=_resolve_path(
            _get_with_default(raw, defaults, "exposure_manifest"),
            base_dir,
        ),
        revoke_recipe_path=_resolve_path(_get_with_default(raw, defaults, "revoke_recipe"), base_dir),
        revoke_key_id=_optional_str(raw.get("revoke_key_id")),
        revoke_key_label=_optional_str(raw.get("revoke_key_label")),
        revoke_profile_dir=_resolve_path(
            _get_with_default(raw, defaults, "revoke_profile_dir"),
            base_dir,
        ),
        revoke_headless=(
            None if _get_with_default(raw, defaults, "revoke_headless") is None
            else bool(_get_with_default(raw, defaults, "revoke_headless"))
        ),
        enabled=bool(raw.get("enabled", True)),
    )


def _resolve_batch_retirement(item: BrowserBatchItem) -> dict[str, Any]:
    manifest = _load_exposure_manifest(item.exposure_manifest_path)
    return {
        "recipe_path": item.revoke_recipe_path or manifest.get("revoke_recipe"),
        "key_id": item.revoke_key_id or manifest.get("key_id"),
        "key_label": item.revoke_key_label or manifest.get("key_label"),
    }


def _load_exposure_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserIssuerError(f"Failed to load exposure manifest: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise BrowserIssuerError("Exposure manifest must be a version 1 JSON object.")
    result: dict[str, Any] = {}
    for key in ("key_id", "key_label", "revoke_recipe"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    if "revoke_recipe" in result:
        result["revoke_recipe"] = _resolve_path(result["revoke_recipe"], path.parent, required=True)
    return result


def _get_with_default(raw: dict[str, Any], defaults: dict[str, Any], key: str, fallback: Any = None) -> Any:
    return raw[key] if key in raw else defaults.get(key, fallback)


def _resolve_path(value: Any, base_dir: Path, *, required: bool = False) -> Path | None:
    text = _optional_str(value)
    if text is None:
        if required:
            raise BrowserIssuerError("browser batch item requires recipe.")
        return None
    path = Path(text)
    return path if path.is_absolute() else base_dir / path


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BrowserIssuerError(f"browser batch item requires non-empty {key}.")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Provider rotation capability matrix for banto sync.

This module classifies sync-managed environment variables into rotation classes:
full_auto, partial_auto, propagate_only, inventory_only, manual_cutover,
or review_required.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from importlib import resources
from typing import Any

from .config import SyncConfig

ROTATION_CLASS_ORDER = (
    "full_auto",
    "partial_auto",
    "propagate_only",
    "inventory_only",
    "manual_cutover",
    "review_required",
)


@dataclass(frozen=True)
class RotationClassInfo:
    """Definition of one supported rotation class."""

    name: str
    summary: str
    operator_action: str


@dataclass(frozen=True)
class CapabilityRule:
    """Rule that maps one or more env var names to an automation strategy."""

    rule_id: str
    provider: str
    display_name: str
    env_patterns: tuple[str, ...]
    rotation_class: str
    implementation_phase: str
    notes: str = ""


@dataclass(frozen=True)
class SecretClassification:
    """Classification result for a sync-managed secret."""

    secret_name: str
    env_name: str
    provider: str
    rotation_class: str
    implementation_phase: str
    matched_rule: str
    notes: str


@dataclass(frozen=True)
class CapabilityMatrix:
    """Loaded rotation class metadata plus matching rules."""

    rotation_classes: dict[str, RotationClassInfo]
    rules: tuple[CapabilityRule, ...]


_CACHE: CapabilityMatrix | None = None


def _load_raw_matrix() -> dict[str, Any]:
    data_path = resources.files("banto").joinpath("rotation_capabilities.json")
    return json.loads(data_path.read_text(encoding="utf-8"))


def load_capability_matrix() -> CapabilityMatrix:
    """Load and validate the bundled rotation capability matrix."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    raw = _load_raw_matrix()
    classes_raw = raw.get("rotation_classes", {})
    rules_raw = raw.get("rules", [])

    rotation_classes: dict[str, RotationClassInfo] = {}
    for name, data in classes_raw.items():
        if not isinstance(data, dict):
            raise ValueError(f"Invalid rotation class entry: {name}")
        rotation_classes[name] = RotationClassInfo(
            name=name,
            summary=str(data.get("summary", "")),
            operator_action=str(data.get("operator_action", "")),
        )

    missing_classes = [name for name in ROTATION_CLASS_ORDER if name not in rotation_classes]
    if missing_classes:
        raise ValueError(f"Missing rotation classes: {', '.join(missing_classes)}")

    rules: list[CapabilityRule] = []
    for item in rules_raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid capability rule entry")
        rotation_class = str(item.get("rotation_class", ""))
        if rotation_class not in rotation_classes:
            raise ValueError(f"Unknown rotation class in rule: {rotation_class}")
        env_patterns = tuple(str(p).upper() for p in item.get("env_patterns", []))
        if not env_patterns:
            raise ValueError(f"Capability rule has no env_patterns: {item}")
        rules.append(CapabilityRule(
            rule_id=str(item.get("rule_id", "")),
            provider=str(item.get("provider", "")),
            display_name=str(item.get("display_name", "")),
            env_patterns=env_patterns,
            rotation_class=rotation_class,
            implementation_phase=str(item.get("implementation_phase", "")),
            notes=str(item.get("notes", "")),
        ))

    _CACHE = CapabilityMatrix(rotation_classes=rotation_classes, rules=tuple(rules))
    return _CACHE


def classify_secret(secret_name: str, env_name: str) -> SecretClassification:
    """Classify a secret by env var name using the bundled capability matrix."""
    matrix = load_capability_matrix()
    normalized_env = (env_name or secret_name).strip().upper()

    for rule in matrix.rules:
        for pattern in rule.env_patterns:
            if fnmatchcase(normalized_env, pattern):
                return SecretClassification(
                    secret_name=secret_name,
                    env_name=normalized_env,
                    provider=rule.provider,
                    rotation_class=rule.rotation_class,
                    implementation_phase=rule.implementation_phase,
                    matched_rule=rule.rule_id,
                    notes=rule.notes,
                )

    return SecretClassification(
        secret_name=secret_name,
        env_name=normalized_env,
        provider="review",
        rotation_class="review_required",
        implementation_phase="phase_0",
        matched_rule="",
        notes="No capability rule matched; review before automating.",
    )


def classify_config(config: SyncConfig) -> list[SecretClassification]:
    """Classify every secret present in a sync config."""
    results = [
        classify_secret(name, entry.env_name)
        for name, entry in sorted(config.secrets.items(), key=lambda item: item[1].env_name)
    ]
    return results


def summarize_classifications(
    classifications: list[SecretClassification],
) -> dict[str, int]:
    """Count secrets per rotation class."""
    counts = {name: 0 for name in ROTATION_CLASS_ORDER}
    for item in classifications:
        counts[item.rotation_class] = counts.get(item.rotation_class, 0) + 1
    return counts

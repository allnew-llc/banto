# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Incident-oriented rotation prioritization for sync-managed secrets."""
from __future__ import annotations

from dataclasses import dataclass

from .capabilities import SecretClassification, classify_config, load_capability_matrix
from .config import SyncConfig

INCIDENT_LANE_ORDER = (
    "rotate_now",
    "approval_gated",
    "manual_cutover",
    "monitor_only",
    "review_required",
)

LANE_LABELS = {
    "rotate_now": "Rotate Now",
    "approval_gated": "Approval Gated",
    "manual_cutover": "Manual Cutover",
    "monitor_only": "Monitor Only",
    "review_required": "Review Required",
}


@dataclass(frozen=True)
class IncidentSecretPlan:
    """Actionable incident-response view for one sync-managed secret."""

    secret_name: str
    env_name: str
    provider: str
    rotation_class: str
    implementation_phase: str
    incident_lane: str
    incident_priority: int
    operator_action: str
    notes: str
    recommended_command: str
    requires_human_value: bool
    targets: tuple[str, ...]
    shared_account_secret_names: tuple[str, ...]


@dataclass(frozen=True)
class IncidentReport:
    """Prioritized incident-response report for one sync config."""

    plans: tuple[IncidentSecretPlan, ...]

    def by_lane(self, lane: str) -> list[IncidentSecretPlan]:
        return [plan for plan in self.plans if plan.incident_lane == lane]

    def counts_by_lane(self) -> dict[str, int]:
        return {lane: len(self.by_lane(lane)) for lane in INCIDENT_LANE_ORDER}


def _lane_for_class(rotation_class: str) -> tuple[str, int]:
    if rotation_class == "full_auto":
        return "rotate_now", 10
    if rotation_class == "propagate_only":
        return "rotate_now", 20
    if rotation_class == "partial_auto":
        return "approval_gated", 30
    if rotation_class == "manual_cutover":
        return "manual_cutover", 40
    if rotation_class == "inventory_only":
        return "monitor_only", 50
    return "review_required", 60


def _build_shared_account_map(config: SyncConfig) -> dict[str, tuple[str, ...]]:
    by_account: dict[str, list[str]] = {}
    for entry in config.secrets.values():
        by_account.setdefault(entry.account, []).append(entry.name)

    result: dict[str, tuple[str, ...]] = {}
    for names in by_account.values():
        ordered = tuple(sorted(names))
        for name in ordered:
            result[name] = tuple(other for other in ordered if other != name)
    return result


def _recommended_command(item: SecretClassification) -> tuple[str, bool]:
    if item.rotation_class == "full_auto" and item.provider == "openai":
        return (
            "banto sync openai-service-account "
            f"{item.secret_name} --project-id <proj_...> "
            "--smoke-preset provider-validate",
            False,
        )
    if item.rotation_class == "full_auto" and item.provider == "google":
        return (
            "banto sync google-api-key "
            f"{item.secret_name} --project-id <project> "
            "--smoke-preset provider-validate",
            False,
        )
    if item.rotation_class == "full_auto" and item.provider == "xai":
        return (
            "banto sync xai-api-key "
            f"{item.secret_name} --team-id <team_id> "
            "--wait-propagation --smoke-preset provider-validate",
            False,
        )
    if item.rotation_class in {"propagate_only", "partial_auto"}:
        return (
            "banto sync propagate "
            f"{item.secret_name} --smoke-preset provider-validate",
            True,
        )
    if item.rotation_class == "manual_cutover":
        return "Follow docs/manual-cutover-rotation-runbook.md", False
    if item.rotation_class == "inventory_only":
        return "Track coverage only; no rotation during this incident lane.", False
    return "Classify and review before automating.", False


def build_incident_report(config: SyncConfig) -> IncidentReport:
    """Build a prioritized incident report from sync-managed secrets."""
    matrix = load_capability_matrix()
    shared_account_map = _build_shared_account_map(config)
    classifications = classify_config(config)
    plans: list[IncidentSecretPlan] = []

    for item in classifications:
        entry = config.get_secret(item.secret_name)
        if entry is None:
            continue
        lane, priority = _lane_for_class(item.rotation_class)
        rotation_info = matrix.rotation_classes[item.rotation_class]
        command, needs_value = _recommended_command(item)
        plans.append(IncidentSecretPlan(
            secret_name=item.secret_name,
            env_name=item.env_name,
            provider=item.provider,
            rotation_class=item.rotation_class,
            implementation_phase=item.implementation_phase,
            incident_lane=lane,
            incident_priority=priority,
            operator_action=rotation_info.operator_action,
            notes=item.notes,
            recommended_command=command,
            requires_human_value=needs_value,
            targets=tuple(target.label for target in entry.targets),
            shared_account_secret_names=shared_account_map.get(item.secret_name, ()),
        ))

    plans.sort(
        key=lambda plan: (
            plan.incident_priority,
            plan.env_name,
            plan.secret_name,
        ),
    )
    return IncidentReport(plans=tuple(plans))

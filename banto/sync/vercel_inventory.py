# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Read-only Vercel environment-variable inventory for incident rotation."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .capabilities import SecretClassification, classify_secret
from .config import SyncConfig
from .drivers.vercel import VercelDriver


@dataclass(frozen=True)
class VercelEnvInventoryItem:
    """One Vercel env var entry without secret value material."""

    project: str
    env_name: str
    targets: tuple[str, ...]
    value_type: str
    created_at_ms: int | None
    updated_at_ms: int | None
    managed_by_sync: bool
    excluded: bool
    classification: SecretClassification

    @property
    def age_days(self) -> int | None:
        if self.created_at_ms is None:
            return None
        created = datetime.fromtimestamp(self.created_at_ms / 1000, tz=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - created).days)

    @property
    def lane(self) -> str:
        if self.excluded:
            return "excluded"
        if self.classification.rotation_class in {"full_auto", "partial_auto", "propagate_only"}:
            return "rotate_now"
        if self.classification.rotation_class == "manual_cutover":
            return "manual_cutover"
        if self.classification.rotation_class == "inventory_only":
            return "monitor_only"
        return "review_required"

    @property
    def needs_sensitive_upgrade(self) -> bool:
        return (
            not self.excluded
            and self.value_type != "sensitive"
            and self.classification.rotation_class not in {"inventory_only"}
        )


@dataclass(frozen=True)
class VercelInventoryReport:
    """Aggregated read-only Vercel env inventory."""

    items: tuple[VercelEnvInventoryItem, ...]

    def counts_by_lane(self) -> dict[str, int]:
        counts = {
            "rotate_now": 0,
            "manual_cutover": 0,
            "monitor_only": 0,
            "review_required": 0,
            "excluded": 0,
        }
        for item in self.items:
            counts[item.lane] = counts.get(item.lane, 0) + 1
        return counts

    def counts_by_project(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.project] = counts.get(item.project, 0) + 1
        return counts

    def sensitive_upgrade_count(self) -> int:
        return sum(1 for item in self.items if item.needs_sensitive_upgrade)

    def unmanaged_count(self) -> int:
        return sum(1 for item in self.items if not item.managed_by_sync)


def parse_vercel_env_json_output(output: str) -> list[dict]:
    """Parse `vercel env ls --format json`, tolerating progress text."""
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Vercel CLI did not return JSON.")
    payload = json.loads(output[start:end + 1])
    envs = payload.get("envs", [])
    if not isinstance(envs, list):
        raise ValueError("Vercel CLI JSON is missing an envs list.")
    return [item for item in envs if isinstance(item, dict)]


def collect_vercel_project_envs(project: str) -> list[dict]:
    """Collect one project's env metadata without reading secret values."""
    driver = VercelDriver()

    def _collect(vercel_bin: str, cwd: str, linked: bool) -> list[dict]:
        if not linked:
            raise RuntimeError(f"Failed to link Vercel project: {project}")
        result = subprocess.run(
            [vercel_bin, "env", "ls", "--format", "json", "--no-color", "--cwd", cwd],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Failed to list Vercel env vars for {project}: {detail}")
        return parse_vercel_env_json_output(result.stdout)

    return driver._with_linked_dir(project, _collect)


def build_vercel_inventory_report(
    config: SyncConfig,
    projects: Iterable[str],
    *,
    exclude_envs: Iterable[str] = (),
) -> VercelInventoryReport:
    """Build a classified inventory for Vercel projects."""
    managed_envs = {entry.env_name.upper() for entry in config.secrets.values()}
    excluded = {name.upper() for name in exclude_envs}
    items: list[VercelEnvInventoryItem] = []

    for project in projects:
        normalized_project = project.strip()
        if not normalized_project:
            continue
        for raw in collect_vercel_project_envs(normalized_project):
            env_name = str(raw.get("key", "")).strip()
            if not env_name:
                continue
            targets_raw = raw.get("target", [])
            targets = tuple(str(target) for target in targets_raw if str(target))
            classification = classify_secret(env_name.lower().replace("_", "-"), env_name)
            items.append(VercelEnvInventoryItem(
                project=normalized_project,
                env_name=env_name,
                targets=targets,
                value_type=str(raw.get("type", "")),
                created_at_ms=raw.get("createdAt") if isinstance(raw.get("createdAt"), int) else None,
                updated_at_ms=raw.get("updatedAt") if isinstance(raw.get("updatedAt"), int) else None,
                managed_by_sync=env_name.upper() in managed_envs,
                excluded=env_name.upper() in excluded,
                classification=classification,
            ))

    return VercelInventoryReport(items=tuple(items))


def report_to_json(report: VercelInventoryReport) -> dict:
    """Serialize a Vercel inventory report without values."""
    return {
        "count": len(report.items),
        "counts_by_lane": report.counts_by_lane(),
        "counts_by_project": report.counts_by_project(),
        "unmanaged_count": report.unmanaged_count(),
        "sensitive_upgrade_count": report.sensitive_upgrade_count(),
        "items": [
            {
                "project": item.project,
                "env_name": item.env_name,
                "targets": list(item.targets),
                "type": item.value_type,
                "age_days": item.age_days,
                "managed_by_sync": item.managed_by_sync,
                "excluded": item.excluded,
                "lane": item.lane,
                "provider": item.classification.provider,
                "rotation_class": item.classification.rotation_class,
                "matched_rule": item.classification.matched_rule,
                "needs_sensitive_upgrade": item.needs_sensitive_upgrade,
            }
            for item in report.items
        ],
    }

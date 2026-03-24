# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""sync.json configuration read/write.

The sync config file (~/.config/banto/sync.json) contains secret metadata
only — never secret values. It maps Keychain accounts to environment variable
names and deployment targets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "banto"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "sync.json"
DEFAULT_KEYCHAIN_SERVICE = "banto-sync"


@dataclass
class Target:
    """A deployment target for a secret."""

    platform: str  # "cloudflare-pages", "vercel", "local"
    project: str = ""  # project name (cloudflare/vercel) or file path (local)
    file: str = ""  # only for platform=local

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Target:
        return cls(
            platform=data.get("platform", ""),
            project=data.get("project", ""),
            file=data.get("file", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"platform": self.platform}
        if self.platform == "local":
            if self.file:
                d["file"] = self.file
        else:
            if self.project:
                d["project"] = self.project
        return d

    @property
    def label(self) -> str:
        if self.platform == "local":
            return self.file or "local"
        return f"{self.platform}:{self.project}" if self.project else self.platform


@dataclass
class SecretEntry:
    """Metadata for one secret in the sync config."""

    name: str
    account: str
    env_name: str
    description: str = ""
    targets: list[Target] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> SecretEntry:
        targets = [Target.from_dict(t) for t in data.get("targets", [])]
        return cls(
            name=name,
            account=data.get("account", name),
            env_name=data.get("env_name", ""),
            description=data.get("description", ""),
            targets=targets,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "account": self.account,
            "env_name": self.env_name,
        }
        if self.description:
            d["description"] = self.description
        if self.targets:
            d["targets"] = [t.to_dict() for t in self.targets]
        return d


@dataclass
class NotifierConfig:
    """Configuration for a notification integration."""

    name: str  # "slack", "teams", "datadog", "pagerduty"
    webhook_url: str  # Webhook URL, API key, or integration key
    events: list[str] = field(default_factory=lambda: ["sync_fail", "audit_drift", "rotate"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotifierConfig:
        return cls(
            name=data.get("name", ""),
            webhook_url=data.get("webhook_url", ""),
            events=data.get("events", ["sync_fail", "audit_drift", "rotate"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "webhook_url": self.webhook_url,
            "events": self.events,
        }


@dataclass
class Environment:
    """An environment with optional secret/target overrides."""

    name: str
    inherits: str = ""  # parent environment name (empty = no inheritance)
    secrets: dict[str, SecretEntry] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Environment:
        secrets: dict[str, SecretEntry] = {}
        for sname, sdata in data.get("secrets", {}).items():
            if isinstance(sdata, dict):
                secrets[sname] = SecretEntry.from_dict(sname, sdata)
        return cls(
            name=name,
            inherits=data.get("inherits", ""),
            secrets=secrets,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.inherits:
            d["inherits"] = self.inherits
        if self.secrets:
            d["secrets"] = {n: e.to_dict() for n, e in self.secrets.items()}
        return d


@dataclass
class SyncConfig:
    """In-memory representation of sync.json."""

    version: int = 1
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE
    secrets: dict[str, SecretEntry] = field(default_factory=dict)
    notifiers: list[NotifierConfig] = field(default_factory=list)
    environments: dict[str, Environment] = field(default_factory=dict)
    default_environment: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> SyncConfig:
        config_path = path or DEFAULT_CONFIG_PATH
        if not config_path.exists():
            return cls()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        secrets: dict[str, SecretEntry] = {}
        for name, data in raw.get("secrets", {}).items():
            if isinstance(data, dict):
                secrets[name] = SecretEntry.from_dict(name, data)
        notifiers = [
            NotifierConfig.from_dict(n)
            for n in raw.get("notifiers", [])
            if isinstance(n, dict)
        ]
        environments: dict[str, Environment] = {}
        for env_name, env_data in raw.get("environments", {}).items():
            if isinstance(env_data, dict):
                environments[env_name] = Environment.from_dict(env_name, env_data)
        return cls(
            version=raw.get("version", 1),
            keychain_service=raw.get("keychain_service", DEFAULT_KEYCHAIN_SERVICE),
            secrets=secrets,
            notifiers=notifiers,
            environments=environments,
            default_environment=raw.get("default_environment", ""),
        )

    def save(self, path: Path | None = None) -> None:
        config_path = path or DEFAULT_CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": self.version,
            "keychain_service": self.keychain_service,
            "secrets": {name: entry.to_dict() for name, entry in self.secrets.items()},
        }
        if self.notifiers:
            data["notifiers"] = [n.to_dict() for n in self.notifiers]
        if self.environments:
            data["environments"] = {n: e.to_dict() for n, e in self.environments.items()}
        if self.default_environment:
            data["default_environment"] = self.default_environment
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_secret(self, entry: SecretEntry) -> None:
        self.secrets[entry.name] = entry

    def remove_secret(self, name: str) -> SecretEntry | None:
        return self.secrets.pop(name, None)

    def get_secret(self, name: str) -> SecretEntry | None:
        return self.secrets.get(name)

    def resolve_environment(self, env_name: str) -> dict[str, SecretEntry]:
        """Resolve secrets for an environment with inheritance.

        Walks the inheritance chain and merges secrets.
        Child overrides take precedence over parent.
        Base (top-level) secrets are always included as the root.
        """
        # Start with base secrets
        resolved = dict(self.secrets)

        # Build inheritance chain
        chain: list[Environment] = []
        current = env_name
        visited: set[str] = set()
        while current and current in self.environments and current not in visited:
            visited.add(current)
            env = self.environments[current]
            chain.append(env)
            current = env.inherits

        # Apply from root to leaf (most specific last)
        for env in reversed(chain):
            for name, entry in env.secrets.items():
                resolved[name] = entry

        return resolved

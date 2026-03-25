# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Sync state — tracks fingerprints and timestamps of last push per secret.

Used by audit to detect drift between Keychain and deployed targets.
Never stores secret values — only SHA-256 fingerprints.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SYNC_STATE_PATH = Path.home() / ".config" / "banto" / "sync-state.json"


def fingerprint(value: str) -> str:
    """SHA-256 fingerprint (first 12 hex chars). Never reveals the value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass
class PushRecord:
    """Record of a push for one secret."""

    fingerprint: str  # SHA-256 of value at push time
    pushed_at: str  # ISO 8601 timestamp
    targets: list[str]  # target labels that were synced

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "pushed_at": self.pushed_at,
            "targets": self.targets,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PushRecord:
        return cls(
            fingerprint=data.get("fingerprint", ""),
            pushed_at=data.get("pushed_at", ""),
            targets=data.get("targets", []),
        )


class SyncState:
    """Persistent push fingerprint state for drift detection."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_SYNC_STATE_PATH
        self._records: dict[str, PushRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for name, rec in data.get("secrets", {}).items():
                self._records[name] = PushRecord.from_dict(rec)
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "secrets": {n: r.to_dict() for n, r in self._records.items()},
        }
        content = json.dumps(data, indent=2, ensure_ascii=False)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    def record_push(self, secret_name: str, value: str, targets: list[str]) -> PushRecord:
        """Record that a secret was pushed with this value to these targets."""
        rec = PushRecord(
            fingerprint=fingerprint(value),
            pushed_at=datetime.now(timezone.utc).isoformat(),
            targets=targets,
        )
        self._records[secret_name] = rec
        self._save()
        return rec

    def get_push_record(self, secret_name: str) -> PushRecord | None:
        """Get the last push record for a secret."""
        return self._records.get(secret_name)

    def remove(self, secret_name: str) -> None:
        """Remove push record for a secret."""
        self._records.pop(secret_name, None)
        self._save()

    def check_drift(self, secret_name: str, current_value: str) -> str:
        """Compare current Keychain value against last-pushed fingerprint.

        Returns:
            "in_sync" — fingerprints match
            "drift_local" — Keychain changed since last push
            "never_pushed" — no push record exists
        """
        rec = self._records.get(secret_name)
        if rec is None:
            return "never_pushed"
        current_fp = fingerprint(current_value)
        if current_fp == rec.fingerprint:
            return "in_sync"
        return "drift_local"

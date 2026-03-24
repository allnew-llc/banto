# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
lease.py - Dynamic secrets with TTL and auto-revocation.

Generates short-lived credentials via external commands, stores them
temporarily in Keychain, and auto-revokes after the TTL expires.

Usage:
    from banto.lease import LeaseManager
    from banto import SecureVault

    vault = SecureVault()
    leases = LeaseManager(vault)

    # Acquire a temporary credential
    info = leases.acquire(
        name="db-staging",
        ttl_seconds=3600,
        cmd="aws iam create-access-key --user-name bot --output json",
        revoke_cmd="aws iam delete-access-key --user-name bot --access-key-id {value}",
    )
    # info.value contains the credential
    # info.lease_id tracks it

    # Later, or automatically on TTL expiry:
    leases.revoke(info.lease_id)

    # Clean up all expired leases:
    leases.cleanup()
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .keychain import KeychainStore

DEFAULT_LEASE_STATE_PATH = Path.home() / ".config" / "banto" / "lease-state.json"


@dataclass
class LeaseInfo:
    """Metadata for an active lease."""

    lease_id: str
    name: str
    value: str  # The credential (only in memory, never persisted to state file)
    created_at: str
    expires_at: str
    ttl_seconds: int
    revoke_cmd: str = ""

    def to_metadata(self) -> dict:
        """Serialize for state file — NEVER includes value."""
        return {
            "lease_id": self.lease_id,
            "name": self.name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "ttl_seconds": self.ttl_seconds,
            "revoke_cmd": self.revoke_cmd,
            "status": "active",
        }


@dataclass
class LeaseState:
    """Persistent state for lease tracking."""

    leases: dict[str, dict] = field(default_factory=dict)  # lease_id -> metadata

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            {"leases": self.leases}, indent=2, ensure_ascii=False,
        )
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    @classmethod
    def load(cls, path: Path) -> LeaseState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(leases=data.get("leases", {}))
        except (json.JSONDecodeError, OSError):
            return cls()


class LeaseManager:
    """Manages dynamic secrets with TTL and auto-revocation.

    Credentials are generated via external commands (--cmd), stored
    temporarily in Keychain, tracked in a state file, and revoked
    via external commands (--revoke-cmd) when TTL expires or on
    explicit revocation.
    """

    KEYCHAIN_PREFIX = "banto-lease"

    def __init__(
        self,
        state_path: Path | None = None,
    ) -> None:
        self._kc = KeychainStore(service_prefix=self.KEYCHAIN_PREFIX)
        self._state_path = state_path or DEFAULT_LEASE_STATE_PATH
        self._state = LeaseState.load(self._state_path)

    def acquire(
        self,
        name: str,
        *,
        ttl_seconds: int = 3600,
        cmd: str,
        revoke_cmd: str = "",
    ) -> LeaseInfo:
        """Acquire a new short-lived credential.

        Args:
            name: Human-readable name for this lease (e.g. "db-staging").
            ttl_seconds: Time-to-live in seconds (default: 1 hour).
            cmd: Shell command to generate the credential. Its stdout
                 is captured as the credential value.
            revoke_cmd: Shell command to revoke the credential on expiry.
                        Use {value} as placeholder for the credential and
                        {lease_id} for the lease ID.

        Returns:
            LeaseInfo with the credential value and lease metadata.

        Raises:
            RuntimeError: If the generation command fails.
        """
        # Generate credential via external command
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            raise RuntimeError(f"Failed to parse command: {e}") from e

        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Command timed out (60s)")

        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:200]}"
            )

        value = result.stdout.strip()
        if not value:
            raise RuntimeError("Command produced empty output")

        # Create lease
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)

        lease_id = f"lease-{uuid.uuid4().hex[:12]}"

        info = LeaseInfo(
            lease_id=lease_id,
            name=name,
            value=value,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            ttl_seconds=ttl_seconds,
            revoke_cmd=revoke_cmd,
        )

        # Store in Keychain (temporary)
        self._kc.store(lease_id, value)

        # Track in state (no value stored)
        self._state.leases[lease_id] = info.to_metadata()
        self._state.save(self._state_path)

        return info

    def get_value(self, lease_id: str) -> str | None:
        """Retrieve a lease's credential from Keychain. None if expired/revoked."""
        meta = self._state.leases.get(lease_id)
        if meta is None or meta.get("status") != "active":
            return None

        # Check TTL
        try:
            expires = datetime.fromisoformat(meta["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                self._do_revoke(lease_id, meta)
                return None
        except (ValueError, KeyError):
            return None

        return self._kc.get(lease_id)

    def revoke(self, lease_id: str) -> bool:
        """Explicitly revoke a lease."""
        meta = self._state.leases.get(lease_id)
        if meta is None:
            return False
        return self._do_revoke(lease_id, meta)

    def _do_revoke(self, lease_id: str, meta: dict) -> bool:
        """Execute revocation: run revoke_cmd, delete from Keychain, update state."""
        revoke_cmd = meta.get("revoke_cmd", "")

        if revoke_cmd:
            # Retrieve value for placeholder substitution
            value = self._kc.get(lease_id) or ""
            expanded = revoke_cmd.replace("{value}", value).replace(
                "{lease_id}", lease_id
            )
            try:
                argv = shlex.split(expanded)
                subprocess.run(
                    argv, capture_output=True, text=True, timeout=30,
                )
            except (subprocess.SubprocessError, OSError, ValueError):
                pass  # Best-effort revocation

        # Remove from Keychain
        self._kc.delete(lease_id)

        # Update state
        meta["status"] = "revoked"
        meta["revoked_at"] = datetime.now(timezone.utc).isoformat()
        self._state.leases[lease_id] = meta
        self._state.save(self._state_path)
        return True

    def list_leases(self) -> list[dict]:
        """List all active leases (metadata only, no values)."""
        now = datetime.now(timezone.utc)
        active = []
        for lease_id, meta in self._state.leases.items():
            if meta.get("status") != "active":
                continue
            # Check if expired
            try:
                expires = datetime.fromisoformat(meta["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                remaining = int((expires - now).total_seconds())
                if remaining <= 0:
                    self._do_revoke(lease_id, meta)
                    continue
                active.append({**meta, "remaining_seconds": remaining})
            except (ValueError, KeyError):
                continue
        return active

    def cleanup(self) -> int:
        """Revoke all expired leases. Returns count of revoked."""
        now = datetime.now(timezone.utc)
        revoked = 0
        for lease_id, meta in list(self._state.leases.items()):
            if meta.get("status") != "active":
                continue
            try:
                expires = datetime.fromisoformat(meta["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if now > expires:
                    self._do_revoke(lease_id, meta)
                    revoked += 1
            except (ValueError, KeyError):
                continue
        return revoked

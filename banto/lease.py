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
        # $BANTO_LEASE_VALUE is expanded by /bin/sh inside the revoke process,
        # so the value never appears in banto's own argv.
        revoke_cmd="aws iam delete-access-key --user-name bot --access-key-id $BANTO_LEASE_VALUE",
    )
    # info.value contains the credential
    # info.lease_id tracks it

    # Later, or automatically on TTL expiry:
    leases.revoke(info.lease_id)

    # Clean up all expired leases:
    leases.cleanup()
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .keychain import KeychainStore

DEFAULT_LEASE_STATE_PATH = Path.home() / ".config" / "banto" / "lease-state.json"
MAX_TTL_SECONDS = 7 * 24 * 3600
_VALUE_REFS = ("$BANTO_LEASE_VALUE", "${BANTO_LEASE_VALUE}")


def validate_ttl(ttl_seconds) -> int:
    """TTL must be an integer between 1 second and 7 days."""
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be an integer between 1 and {MAX_TTL_SECONDS}")
    return ttl_seconds


def revoke_argv(revoke_cmd: str, lease_id: str) -> list[str]:
    """Build the revoke argv.

    Commands that reference ``$BANTO_LEASE_VALUE`` run via ``/bin/sh -c`` so the
    shell expands it from the environment (documented behaviour). Others are
    split with shlex and executed directly, as before.
    """
    expanded = revoke_cmd.replace("{lease_id}", lease_id)
    if any(ref in expanded for ref in _VALUE_REFS):
        return ["/bin/sh", "-c", expanded]
    return shlex.split(expanded)


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
        """Atomic write: temp file in the same directory, fsync, then rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            {"leases": self.leases}, indent=2, ensure_ascii=False,
        )
        fd, tmp = tempfile.mkstemp(prefix=".lease-state-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

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

    @contextmanager
    def _transaction(self):
        """Serialize read-modify-write of the state file across processes."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._state_path.with_name(self._state_path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._state = LeaseState.load(self._state_path)
            yield self._state
            self._state.save(self._state_path)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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
                        The credential value is passed via the BANTO_LEASE_VALUE
                        environment variable (use $BANTO_LEASE_VALUE in the command).
                        Use {lease_id} as placeholder for the lease ID.

        Returns:
            LeaseInfo with the credential value and lease metadata.

        Raises:
            RuntimeError: If the generation command fails.
        """
        validate_ttl(ttl_seconds)
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
                f"Command failed (exit {result.returncode})"
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
        with self._transaction() as state:
            state.leases[lease_id] = info.to_metadata()

        return info

    @staticmethod
    def _expired(meta: dict, now: datetime) -> bool | None:
        try:
            expires = datetime.fromisoformat(meta["expires_at"])
        except (ValueError, KeyError, TypeError):
            return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now > expires

    def get_value(self, lease_id: str) -> str | None:
        """Retrieve a lease's credential from Keychain. None if expired/revoked."""
        meta = self._state.leases.get(lease_id)
        if meta is None or meta.get("status") != "active":
            return None
        expired = self._expired(meta, datetime.now(timezone.utc))
        if expired is None:
            return None
        if expired:
            self.revoke(lease_id)
            return None
        return self._kc.get(lease_id)

    def revoke(self, lease_id: str) -> bool:
        """Explicitly revoke a lease.

        Returns True when revoked. Returns False when the lease is unknown or
        the revoke command failed; a failed lease is kept as ``revoke_failed``
        (credential retained in Keychain) so ``cleanup()`` can retry it.
        """
        with self._transaction() as state:
            meta = state.leases.get(lease_id)
            if meta is None or meta.get("status") == "revoked":
                return meta is not None
            return self._do_revoke(lease_id, meta)

    def _do_revoke(self, lease_id: str, meta: dict) -> bool:
        """Run revoke_cmd; delete the credential only after it succeeds."""
        revoke_cmd = meta.get("revoke_cmd", "")
        now = datetime.now(timezone.utc).isoformat()

        if revoke_cmd:
            # Value is passed via env var, NOT expanded into banto's argv.
            value = self._kc.get(lease_id) or ""
            env = {**os.environ, "BANTO_LEASE_VALUE": value}
            error = None
            try:
                proc = subprocess.run(
                    revoke_argv(revoke_cmd, lease_id), capture_output=True,
                    text=True, timeout=30, env=env,
                )
                if proc.returncode != 0:
                    error = f"exit {proc.returncode}"
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                error = type(exc).__name__
            if error:
                # Fail closed: keep the credential and retry later.
                meta["status"] = "revoke_failed"
                meta["revoke_error"] = error
                meta["revoke_attempts"] = int(meta.get("revoke_attempts", 0)) + 1
                meta["last_revoke_attempt_at"] = now
                return False

        self._kc.delete(lease_id)
        meta["status"] = "revoked"
        meta["revoked_at"] = now
        meta.pop("revoke_error", None)
        return True

    def list_leases(self) -> list[dict]:
        """List active, unexpired leases (metadata only). No side effects."""
        now = datetime.now(timezone.utc)
        active = []
        for meta in self._state.leases.values():
            if meta.get("status") != "active" or self._expired(meta, now) is not False:
                continue
            expires = datetime.fromisoformat(meta["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            active.append({**meta, "remaining_seconds": int((expires - now).total_seconds())})
        return active

    def pending_revocation(self) -> list[dict]:
        """Expired-but-active and ``revoke_failed`` leases awaiting cleanup()."""
        now = datetime.now(timezone.utc)
        pending = []
        for meta in self._state.leases.values():
            status = meta.get("status")
            if status == "revoke_failed" or (status == "active" and self._expired(meta, now)):
                pending.append({k: meta.get(k) for k in
                                ("lease_id", "name", "expires_at", "status", "revoke_error", "revoke_attempts")
                                if k in meta})
        return pending

    def cleanup(self) -> int:
        """Revoke expired leases and retry failed revocations. Returns count revoked."""
        now = datetime.now(timezone.utc)
        revoked = 0
        with self._transaction() as state:
            for lease_id, meta in list(state.leases.items()):
                status = meta.get("status")
                due = status == "revoke_failed" or (status == "active" and self._expired(meta, now))
                if due and self._do_revoke(lease_id, meta):
                    revoked += 1
        return revoked

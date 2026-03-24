# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Audit logging for banto sync operations.

Log format: ISO8601 ACTION secret_name target result
Values are NEVER logged.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

DEFAULT_AUDIT_LOG = Path.home() / ".config" / "banto" / "sync-audit.log"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def log_event(
    action: str,
    secret_name: str,
    target: str,
    result: str,
    *,
    log_path: Path | None = None,
) -> None:
    """Append an audit event to the sync audit log."""
    path = log_path or DEFAULT_AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_now_iso()} {action} {secret_name} {target} {result}\n"
    # Append with restrictive permissions
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_log(log_path: Path | None = None) -> list[str]:
    """Read all audit log lines."""
    path = log_path or DEFAULT_AUDIT_LOG
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()

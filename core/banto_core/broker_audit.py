# Copyright (c) 2025-2026 AllNew LLC. All rights reserved.
# Licensed under the banto Dual License; see LICENSE.
"""Metadata-only, hash-chained audit log for broker operations.

Each JSON line records *what* happened (operation, outcome, provider/model,
cost) and never argument values, secret values, prompts, or response bodies.
Every record carries the SHA-256 of the previous record (``prev``) and its own
``hash`` over the canonical JSON, so edits, deletions, and reordering are
detectable with :func:`verify`. This makes tampering detectable, not
impossible: a same-UID attacker can rewrite the whole chain.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
MAX_BYTES = 5 * 1024 * 1024
# Only these metadata keys are ever written; anything else is dropped.
ALLOWED_META = frozenset({
    "provider", "model", "status", "estimated_usd", "settled_usd",
    "hold_id", "budget", "argument_keys", "input_tokens", "output_tokens",
})


def _digest(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _last_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 65536))
        lines = [line for line in stream.read().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"hash": "corrupt", "seq": -1}


def _clean_meta(meta: dict | None) -> dict:
    clean = {}
    for key, value in (meta or {}).items():
        if key not in ALLOWED_META:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            clean[key] = sorted(value)
    return clean


class AuditLog:
    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes

    def append(self, operation: str, ok: bool, error: str | None = None,
               meta: dict | None = None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            last = _last_record(self.path)
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                # Rotation keeps the chain continuous across files.
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seq": (last.get("seq", -1) + 1) if last else 0,
                "operation": str(operation)[:64],
                "ok": bool(ok),
                "error": (str(error).split(";")[0][:80] if error else None),
                "meta": _clean_meta(meta),
                "prev": last.get("hash", GENESIS) if last else GENESIS,
            }
            record["hash"] = _digest(record)
            line = json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, line.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            return record
        finally:
            os.close(lock_fd)

    def tail(self, n: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        lines = [line for line in self.path.read_text().splitlines() if line.strip()]
        return [json.loads(line) for line in lines[-n:]]


def verify(path: Path) -> dict:
    """Verify the hash chain of the current file (and ``.1`` predecessor if present)."""
    path = Path(path)
    files = [p for p in (path.with_name(path.name + ".1"), path) if p.exists()]
    prev = None
    count = 0
    for file in files:
        for number, line in enumerate(file.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return {"ok": False, "entries": count, "broken_at": f"{file.name}:{number}", "reason": "invalid_json"}
            if record.get("hash") != _digest(record):
                return {"ok": False, "entries": count, "broken_at": f"{file.name}:{number}", "reason": "hash_mismatch"}
            if prev is not None and record.get("prev") != prev:
                return {"ok": False, "entries": count, "broken_at": f"{file.name}:{number}", "reason": "chain_broken"}
            prev = record["hash"]
            count += 1
    return {"ok": True, "entries": count, "broken_at": None, "reason": None}

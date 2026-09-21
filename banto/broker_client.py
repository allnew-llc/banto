"""Local operation client. This module never imports or reads Keychain."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat

MAX_MESSAGE = 8 * 1024 * 1024


class BrokerError(RuntimeError):
    pass


def runtime_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "banto" / "broker"


def socket_path() -> Path:
    return runtime_dir() / "broker.sock"


def read_message(stream) -> dict:
    line = stream.readline(MAX_MESSAGE + 1)
    if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
        raise BrokerError("invalid_message")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise BrokerError("invalid_message")
    return value


def encode_message(value: dict) -> bytes:
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_MESSAGE:
        raise BrokerError("message_too_large")
    return data


def check_private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISSOCK
    if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BrokerError("unsafe_broker_path")


class BrokerClient:
    def __init__(self, path: Path | None = None, *, timeout: float = 180):
        self.path = path or socket_path()
        self.timeout = timeout

    def call(self, operation: str, **arguments) -> dict:
        """Invoke an allowlisted operation. No automatic retry or direct fallback."""
        try:
            check_private(self.path.parent, directory=True)
            check_private(self.path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(str(self.path))
                connection.sendall(encode_message({"operation": operation, "arguments": arguments}))
                with connection.makefile("rb") as stream:
                    result = read_message(stream)
        except (OSError, ValueError):
            raise BrokerError("broker_unavailable_or_outcome_unknown; do not retry mutations blindly") from None
        if not result.get("ok"):
            raise BrokerError(result.get("error", "operation_failed"))
        return result["result"]

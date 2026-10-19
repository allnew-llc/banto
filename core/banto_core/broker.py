# Copyright (c) 2025-2026 AllNew LLC. All rights reserved.
# Licensed under the banto Dual License; see LICENSE.
"""Reusable same-UID local broker transport. No provider operations or Keychain imports."""
from __future__ import annotations
import contextvars
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import socketserver
import struct
import subprocess
import sys
import time

from .broker_client import BrokerError, check_private, runtime_dir
from .broker_client import encode_message as encode_message, read_message as read_message
from .redaction import redact

_observed = contextvars.ContextVar("banto_core_broker_secrets", default=None)
_audit_meta = contextvars.ContextVar("banto_core_broker_audit_meta", default=None)


def require_service(*, in_service=False, directory=None):
    """Prevent accidental direct access after the caller enables its broker."""
    directory = runtime_dir() if directory is None else Path(directory)
    if not in_service and (directory / "required").exists():
        raise BrokerError("direct_keychain_disabled; use BrokerClient operations or banto register")


def observe_secret(value, *, observed=None):
    values = (_observed if observed is None else observed).get()
    if values is not None:
        values.add(value)


def audit_metadata(meta, *, context=None):
    current = (_audit_meta if context is None else context).get()
    if current is not None:
        current.update(meta)


def _unbound_dispatch(operation, arguments):
    raise BrokerError("operation_not_allowed")


def dispatch(operation, arguments, *, handler=None, observed=None, redactor=redact, audit_context=None):
    """Redact observed values and always release the request-local observation set."""
    observed = _observed if observed is None else observed
    values = set()
    token = observed.set(values)
    try:
        return redactor((handler or _unbound_dispatch)(operation, arguments), values)
    except BrokerError as error:
        raise BrokerError(redactor(str(error), values)) from None
    finally:
        meta = (_audit_meta if audit_context is None else audit_context).get()
        if meta is not None:
            cleaned = redactor(meta, values)
            meta.clear()
            meta.update(cleaned)
        observed.reset(token)
        values.clear()



def check_rate_limit(calls, rpm, now):
    """Apply a caller-configured rolling 60-second limit to a caller-owned deque."""
    if type(rpm) is not int or rpm < 0:
        raise BrokerError("broker_config_invalid; api_request_rpm must be a non-negative integer")
    if rpm == 0:
        return
    while calls and now - calls[0] >= 60:
        calls.popleft()
    if len(calls) >= rpm:
        retry = max(1, int(60 - (now - calls[0])) + 1)
        raise BrokerError(f"rate_limited; retry after {retry}s (broker.api_request_rpm={rpm})")
    calls.append(now)


def peer_uid(connection) -> int:
    if sys.platform == "darwin":
        # LOCAL_PEERCRED (SOL_LOCAL=0), struct xucred: version, uid, groups.
        raw = connection.getsockopt(0, 1, 80)
        version, uid = struct.unpack_from("=II", raw)
        if version != 0:
            raise BrokerError("unsupported_peer_credentials")
        return uid
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return struct.unpack("3i", raw)[1]


class Handler(socketserver.StreamRequestHandler):
    namespace = staticmethod(lambda: globals())

    def handle(self):
        namespace = self.namespace()
        _audit_meta = namespace["_audit_meta"]
        check_uid = namespace["peer_uid"]
        receive = namespace["read_message"]
        encode = namespace["encode_message"]
        error_type = namespace["BrokerError"]
        owner_uid = namespace["os"].getuid
        self.connection.settimeout(180)
        operation = "unknown"
        meta: dict = {}
        token = _audit_meta.set(meta)
        try:
            if check_uid(self.connection) != owner_uid():
                raise error_type("peer_denied")
            request = receive(self.rfile)
            if set(request) != {"operation", "arguments"} or not isinstance(request["arguments"], dict):
                raise error_type("invalid_request")
            if isinstance(request["operation"], str):
                operation = request["operation"]
            # Argument names only; values may be secrets or prompts.
            meta["argument_keys"] = [k for k in request["arguments"] if isinstance(k, str)][:20]
            # Serialized execution keeps existing sync/rotation state safe.
            result = self.server.dispatch(request["operation"], request["arguments"])
            response = {"ok": True, "result": result}
        except error_type as error:
            response = {"ok": False, "error": str(error)}
        except Exception:
            # Exceptions can contain credentials or upstream response bodies.
            response = {"ok": False, "error": "operation_failed"}
        finally:
            _audit_meta.reset(token)
        if operation != "health":
            try:
                self.server.audit(operation, response["ok"], response.get("error"), meta)
            except Exception:
                pass  # Auditing must never leak data or break the reply.
        try:
            self.wfile.write(encode(response))
        except (OSError, error_type):
            pass


class Server(socketserver.UnixStreamServer):
    dispatch = staticmethod(_unbound_dispatch)
    audit = staticmethod(lambda *args: None)

    def __init__(self, address, handler=Handler, bind_and_activate=True, *, dispatcher=None, audit=None):
        if dispatcher is not None:
            self.dispatch = dispatcher
        if audit is not None:
            self.audit = audit
        super().__init__(address, handler, bind_and_activate=bind_and_activate)

    def handle_error(self, request, client_address):
        pass


def serve(path: Path, *, server_factory=Server, handler_factory=Handler,
          set_service_state=None, path_checker=None) -> None:
    path_checker = check_private if path_checker is None else path_checker
    path = Path(path)
    state = set_service_state or (lambda value: None)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path_checker(path.parent, directory=True)
    lock_fd = os.open(path.parent / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            path_checker(path)
            path.unlink()
        state(True)
        old_umask = os.umask(0o077)
        try:
            server = server_factory(str(path), handler_factory)
        finally:
            os.umask(old_umask)
        with server:
            try:
                server.serve_forever()
            finally:
                path.unlink(missing_ok=True)
    finally:
        state(False)
        os.close(lock_fd)


def install(*, directory: Path, label: str, module: str, working_directory: Path,
            client_factory, runner=None, path_checker=None) -> None:
    """Install a user LaunchAgent with one absolute Python path; no Keychain read."""
    if sys.platform != "darwin":
        raise BrokerError("macos_required")
    runner = subprocess.run if runner is None else runner
    path_checker = check_private if path_checker is None else path_checker
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise BrokerError("invalid_launch_agent_label")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module):
        raise BrokerError("invalid_launch_agent_module")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path_checker(directory, directory=True)
    plist_path = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.exists():
        raise BrokerError("launch_agent_already_exists; inspect before replacing")
    spec = {"Label": label, "ProgramArguments": [sys.executable, "-m", module, "serve"],
            "WorkingDirectory": str(working_directory),
            "RunAtLoad": True, "KeepAlive": True, "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}
    with plist_path.open("xb") as target:
        plistlib.dump(spec, target)
    os.chmod(plist_path, 0o600)
    result = runner(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], capture_output=True)
    if result.returncode:
        plist_path.unlink()
        raise BrokerError("launch_agent_bootstrap_failed")
    result = runner(["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"], capture_output=True)
    if result.returncode:
        raise BrokerError("launch_agent_installed_but_start_failed")
    deadline = time.monotonic() + 10
    while True:
        try:
            client_factory(timeout=0.5).call("health")
            break
        except BrokerError:
            if time.monotonic() >= deadline:
                raise BrokerError("launch_agent_installed_but_not_ready") from None
            time.sleep(0.1)
    print(json.dumps({"installed": True, "plist": str(plist_path), "python": sys.executable}))

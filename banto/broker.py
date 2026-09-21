"""One macOS process owns Keychain operations; clients receive results only.

The Unix socket is a same-user boundary, not an app-signature sandbox. There is
deliberately no get-secret, arbitrary URL, shell, import, or environment API.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import fcntl
import inspect
import json
import os
from pathlib import Path
import plistlib
import socket
import socketserver
import struct
import subprocess
import sys
import urllib.error
import urllib.request

from .broker_client import (BrokerClient, BrokerError, MAX_MESSAGE, check_private,
                            encode_message, read_message, runtime_dir, socket_path)

IN_SERVICE = False
_observed = contextvars.ContextVar("banto_broker_secrets", default=None)
LABEL = "work.allnew.banto.broker"
MCP_OPERATIONS = (
    "banto_sync_status", "banto_sync_push", "banto_sync_audit", "banto_validate",
    "banto_validate_keychain", "banto_budget_status", "banto_sync_setup",
    "banto_register_key", "banto_lease_list", "banto_lease_cleanup",
    "banto_sealed_create_key", "banto_sealed_export_pubkey", "banto_sealed_sign",
    "banto_sealed_list",
)


def require_service() -> None:
    """Fail closed for legacy library calls once this machine enables the broker.

    This prevents accidental direct reads, not hostile code under the same UID.
    """
    if not IN_SERVICE and (runtime_dir() / "required").exists():
        raise BrokerError("direct_keychain_disabled; use BrokerClient operations or banto register")


def observe_secret(value: str) -> None:
    values = _observed.get()
    if values is not None:
        values.add(value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(provider: str, payload: dict) -> dict:
    """Non-streaming generation at fixed official endpoints; no caller auth/URL."""
    if provider not in ("openai", "anthropic") or not isinstance(payload, dict):
        raise BrokerError("unsupported_request")
    allowed = ({"model", "input", "instructions", "max_output_tokens", "temperature",
                "top_p", "reasoning", "text", "store"} if provider == "openai" else
               {"model", "messages", "max_tokens", "system", "temperature", "top_p",
                "top_k", "stop_sequences", "thinking"})
    if set(payload) - allowed or not isinstance(payload.get("model"), str):
        raise BrokerError("unsupported_payload_fields")
    from .vault import SecureVault
    if SecureVault().budget_enabled:
        raise BrokerError("budget_enabled; generation adapter must support hold/settle before use")
    from .keychain import KeychainStore
    from .sync.config import SyncConfig
    key = KeychainStore(service_prefix=SyncConfig.load().keychain_service).get(provider)
    if not key:
        raise BrokerError("key_unavailable; register or approve access locally")
    headers = {"Content-Type": "application/json"}
    if provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers["Authorization"] = "Bearer " + key
        payload = {**payload, "store": False}
    else:
        url = "https://api.anthropic.com/v1/messages"
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    # Ignore ambient proxy settings and never forward auth through redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    try:
        with opener.open(request, timeout=120) as response:
            raw = response.read(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise BrokerError("upstream_response_too_large")
        result = json.loads(raw)
        # Filter after decoding, so JSON escapes cannot defeat redaction.
        return redact(result, {key})
    except urllib.error.HTTPError as error:
        error.close()
        return {"error": "upstream_http_error", "status": error.code}
    except (OSError, ValueError):
        raise BrokerError("upstream_failed_or_outcome_unknown") from None


def redact(value, values: set[str]):
    if isinstance(value, str):
        for secret in sorted(values, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {redact(k, values): redact(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, values) for v in value]
    return value


def _dispatch(operation: str, arguments: dict) -> dict:
    if operation == "health" and not arguments:
        return {"status": "ready", "pid": os.getpid(), "python": sys.executable,
                "python_realpath": str(Path(sys.executable).resolve()),
                "protocol": 1, "direct_banto_access_blocked": (runtime_dir() / "required").exists()}
    if operation == "api_request":
        return api_request(**arguments)
    if operation in ("register_asc", "register_x_oauth") and not arguments:
        from . import register_popup
        name = "serve_asc_register_popup" if operation == "register_asc" else "serve_x_oauth_register_popup"
        return {"url": getattr(register_popup, name)(blocking=False), "status": "popup_opened"}
    if operation not in MCP_OPERATIONS:
        raise BrokerError("operation_not_allowed")
    from . import mcp_server
    function = getattr(mcp_server, operation)
    inspect.signature(function).bind(**arguments)
    return asyncio.run(function(**arguments))


def dispatch(operation: str, arguments: dict) -> dict:
    values: set[str] = set()
    token = _observed.set(values)
    try:
        return redact(_dispatch(operation, arguments), values)
    finally:
        _observed.reset(token)
        values.clear()


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
    def handle(self):
        self.connection.settimeout(180)
        try:
            if peer_uid(self.connection) != os.getuid():
                raise BrokerError("peer_denied")
            request = read_message(self.rfile)
            if set(request) != {"operation", "arguments"} or not isinstance(request["arguments"], dict):
                raise BrokerError("invalid_request")
            # Serialized execution keeps existing sync/rotation state safe.
            result = self.server.dispatch(request["operation"], request["arguments"])
            response = {"ok": True, "result": result}
        except BrokerError as error:
            response = {"ok": False, "error": str(error)}
        except Exception:
            # Exceptions can contain credentials or upstream response bodies.
            response = {"ok": False, "error": "operation_failed"}
        try:
            self.wfile.write(encode_message(response))
        except (OSError, BrokerError):
            pass


class Server(socketserver.UnixStreamServer):
    dispatch = staticmethod(dispatch)

    def handle_error(self, request, client_address):
        pass  # Never emit request data or exception tracebacks.


def serve(path: Path | None = None) -> None:
    global IN_SERVICE
    path = path or socket_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    check_private(path.parent, directory=True)
    lock_fd = os.open(path.parent / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            check_private(path)
            path.unlink()
        IN_SERVICE = True
        old_umask = os.umask(0o077)
        try:
            server = Server(str(path), Handler)
        finally:
            os.umask(old_umask)
        with server:
            try:
                server.serve_forever()
            finally:
                path.unlink(missing_ok=True)
    finally:
        IN_SERVICE = False
        os.close(lock_fd)


def install() -> None:
    """Install a user LaunchAgent with one absolute Python path; no Keychain read."""
    if sys.platform != "darwin":
        raise BrokerError("macos_required")
    # Validate dependencies without importing/reading user credentials.
    import mcp  # noqa: F401
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    check_private(directory, directory=True)
    plist_path = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.exists():
        raise BrokerError("launch_agent_already_exists; inspect before replacing")
    spec = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", "banto.broker", "serve"],
            "WorkingDirectory": str(Path(__file__).resolve().parent.parent),
            "RunAtLoad": True, "KeepAlive": True, "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}
    with plist_path.open("xb") as target:
        plistlib.dump(spec, target)
    os.chmod(plist_path, 0o600)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], capture_output=True)
    if result.returncode:
        plist_path.unlink()
        raise BrokerError("launch_agent_bootstrap_failed")
    print(json.dumps({"installed": True, "plist": str(plist_path), "python": sys.executable}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "install", "health", "enable", "disable", "call"])
    parser.add_argument("operation", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            # -m executes this file as __main__; use the canonical module so
            # Keychain's guard sees the same IN_SERVICE flag.
            from . import broker
            broker.serve()
        elif args.command == "install":
            install()
        elif args.command == "enable":
            BrokerClient().call("health")
            (runtime_dir() / "required").touch(mode=0o600)
            print('{"direct_banto_access_blocked": true}')
        elif args.command == "disable":
            (runtime_dir() / "required").unlink(missing_ok=True)
            print('{"direct_banto_access_blocked": false, "restart_mcp_clients": true}')
        else:
            operation = "health" if args.command == "health" else args.operation
            if not operation:
                parser.error("call requires an operation; arguments are a JSON object on stdin")
            arguments = {} if args.command == "health" else json.load(sys.stdin)
            print(json.dumps(BrokerClient().call(operation, **arguments), ensure_ascii=False))
    except (BrokerError, OSError, ValueError, TypeError):
        print("Broker command failed; no direct Keychain fallback. Check service and operation arguments.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

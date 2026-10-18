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
import select
import socket
import socketserver
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque

from .broker_client import (BrokerClient, BrokerError, MAX_MESSAGE, check_private,
                            encode_message, read_message, runtime_dir, socket_path)

IN_SERVICE = False
_observed = contextvars.ContextVar("banto_broker_secrets", default=None)
# Per-request audit metadata (never values); filled by operations, written by Handler.
_audit_meta = contextvars.ContextVar("banto_broker_audit_meta", default=None)
DEFAULT_API_REQUEST_RPM = 30
_api_calls: deque = deque()
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


def _audit(**meta) -> None:
    current = _audit_meta.get()
    if current is not None:
        current.update(meta)


def audit_path() -> Path:
    return runtime_dir() / "audit.jsonl"


def _broker_settings() -> dict:
    """Read the optional ``broker`` section of config.json (no secrets there)."""
    from .guard import _resolve_config_path
    try:
        with open(_resolve_config_path(None), encoding="utf-8") as stream:
            section = json.load(stream).get("broker", {})
        return section if isinstance(section, dict) else {}
    except (OSError, ValueError):
        return {}


def _check_rate_limit(now: float | None = None) -> None:
    """Sliding 60-second window; ``broker.api_request_rpm`` = 0 disables it."""
    rpm = _broker_settings().get("api_request_rpm", DEFAULT_API_REQUEST_RPM)
    if type(rpm) is not int or rpm < 0:
        raise BrokerError("broker_config_invalid; api_request_rpm must be a non-negative integer")
    if rpm == 0:
        return
    now = time.monotonic() if now is None else now
    while _api_calls and now - _api_calls[0] >= 60:
        _api_calls.popleft()
    if len(_api_calls) >= rpm:
        retry = max(1, int(60 - (now - _api_calls[0])) + 1)
        raise BrokerError(f"rate_limited; retry after {retry}s (broker.api_request_rpm={rpm})")
    _api_calls.append(now)


def _usage_tokens(provider: str, result: dict) -> tuple[int, int] | None:
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None
    fields = (("input_tokens",) if provider == "openai" else
              ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    values = [usage.get(field, 0 if field != "input_tokens" else None) for field in fields]
    output = usage.get("output_tokens")
    if any(type(v) is not int or v < 0 for v in [*values, output]):
        return None
    # Cached input is charged at the full input rate: safe-side over-estimate.
    return sum(values), output


def _budget_hold(guard, provider: str, payload: dict) -> tuple[str, float]:
    """Reserve the worst case before any key read or network call."""
    model = payload["model"]
    limit_field = "max_output_tokens" if provider == "openai" else "max_tokens"
    max_out = payload.get(limit_field)
    if type(max_out) is not int or max_out <= 0:
        raise BrokerError(f"budget_requires_{limit_field}; set a positive integer so the call can be reserved")
    entry = guard.pricing.get(model) if not model.startswith("_") else None
    if not isinstance(entry, dict) or entry.get("type") != "per_token":
        raise BrokerError("unpriced_model; add a per_token entry to pricing.json before budgeted use")
    # Bytes of the serialized request bound its input token count from above.
    input_bound = len(json.dumps(payload, ensure_ascii=False).encode())
    estimated = guard.estimate_cost(model, input_tokens=input_bound, output_tokens=max_out)
    from .guard import BudgetExceededError
    try:
        hold_id = guard.hold_budget(model, provider=provider, input_tokens=input_bound,
                                    output_tokens=max_out, durable=True)
    except BudgetExceededError as error:
        _audit(budget="exceeded", estimated_usd=round(estimated, 6))
        raise BrokerError(f"budget_exceeded; {error}") from None
    _audit(budget="held", hold_id=hold_id, estimated_usd=round(estimated, 6))
    return hold_id, estimated


def api_request(provider: str, payload: dict) -> dict:
    """Non-streaming generation at fixed official endpoints; no caller auth/URL.

    With budget gating enabled the worst-case cost is reserved first (durable
    hold), settled from the provider-reported usage on success, voided only on
    a definite client error (4xx), and kept when the outcome is unknown.
    """
    if provider not in ("openai", "anthropic") or not isinstance(payload, dict):
        raise BrokerError("unsupported_request")
    allowed = ({"model", "input", "instructions", "max_output_tokens", "temperature",
                "top_p", "reasoning", "text", "store"} if provider == "openai" else
               {"model", "messages", "max_tokens", "system", "temperature", "top_p",
                "top_k", "stop_sequences", "thinking"})
    if set(payload) - allowed or not isinstance(payload.get("model"), str):
        raise BrokerError("unsupported_payload_fields")
    _audit(provider=provider, model=payload["model"][:128])
    from .vault import SecureVault
    guard = SecureVault()._guard
    hold_id = None
    if guard is not None:
        hold_id, _ = _budget_hold(guard, provider, payload)
    try:
        _check_rate_limit()
        from .keychain import KeychainStore
        from .sync.config import SyncConfig
        key = KeychainStore(service_prefix=SyncConfig.load().keychain_service).get(provider)
        if not key:
            raise BrokerError("key_unavailable; register or approve access locally")
    except BaseException:
        if hold_id:  # nothing was sent: release the reservation
            guard.void_hold(hold_id)
            _audit(budget="voided")
        raise
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
    except urllib.error.HTTPError as error:
        error.close()
        _audit(status=error.code)
        if hold_id and 400 <= error.code < 500:
            guard.void_hold(hold_id)  # definite rejection: not billed
            _audit(budget="voided")
        elif hold_id:
            _audit(budget="kept_unknown_outcome")
        return {"error": "upstream_http_error", "status": error.code}
    except BrokerError:
        if hold_id:
            _audit(budget="kept_unknown_outcome")
        raise
    except (OSError, ValueError):
        if hold_id:
            _audit(budget="kept_unknown_outcome")
        raise BrokerError("upstream_failed_or_outcome_unknown") from None
    _audit(status=200)
    # Filter after decoding, so JSON escapes cannot defeat redaction.
    result = redact(result, {key})
    if hold_id:
        tokens = _usage_tokens(provider, result)
        if tokens is None:
            _audit(budget="kept_usage_missing")
            budget = {"hold_id": hold_id, "status": "kept_usage_missing"}
        else:
            settled = guard.settle_hold(hold_id, model=payload["model"], provider=provider,
                                        input_tokens=tokens[0], output_tokens=tokens[1],
                                        operation="api_request")
            _audit(budget="settled", settled_usd=round(settled["cost_usd"], 6),
                   input_tokens=tokens[0], output_tokens=tokens[1])
            budget = {"hold_id": hold_id, "status": "settled",
                      "settled_usd": settled["cost_usd"], "reserved_usd": settled["hold_cost_usd"],
                      "remaining_usd": settled["remaining_usd"]}
        if isinstance(result, dict):
            result = {**result, "banto_budget": budget}
    return result


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
    if operation == "register_asc" and not arguments:
        from . import register_popup
        return {"url": register_popup.serve_asc_register_popup(blocking=False), "status": "popup_opened"}
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
        operation = "unknown"
        meta: dict = {}
        token = _audit_meta.set(meta)
        try:
            if peer_uid(self.connection) != os.getuid():
                raise BrokerError("peer_denied")
            request = read_message(self.rfile)
            if set(request) != {"operation", "arguments"} or not isinstance(request["arguments"], dict):
                raise BrokerError("invalid_request")
            if isinstance(request["operation"], str):
                operation = request["operation"]
            # Argument names only; values may be secrets or prompts.
            meta["argument_keys"] = [k for k in request["arguments"] if isinstance(k, str)][:20]
            # Serialized execution keeps existing sync/rotation state safe.
            result = self.server.dispatch(request["operation"], request["arguments"])
            response = {"ok": True, "result": result}
        except BrokerError as error:
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
            self.wfile.write(encode_message(response))
        except (OSError, BrokerError):
            pass


def _write_audit(operation: str, ok: bool, error: str | None, meta: dict) -> None:
    from .broker_audit import AuditLog
    AuditLog(audit_path()).append(operation, ok, error, meta)


class Server(socketserver.UnixStreamServer):
    dispatch = staticmethod(dispatch)
    audit = staticmethod(_write_audit)

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
    result = subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if result.returncode:
        raise BrokerError("launch_agent_installed_but_start_failed")
    deadline = time.monotonic() + 10
    while True:
        try:
            BrokerClient(timeout=0.5).call("health")
            break
        except BrokerError:
            if time.monotonic() >= deadline:
                raise BrokerError("launch_agent_installed_but_not_ready") from None
            time.sleep(0.1)
    print(json.dumps({"installed": True, "plist": str(plist_path), "python": sys.executable}))


# A pipe must deliver a complete object and EOF before any operation is sent.
# In particular, temporary silence must never become an empty argument object.
CALL_STDIN_TIMEOUT = 5.0


def _read_call_arguments() -> dict:
    if sys.stdin.isatty():
        raise ValueError("call requires a JSON object piped on stdin")
    fd = sys.stdin.fileno()
    deadline = time.monotonic() + CALL_STDIN_TIMEOUT
    payload = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError("call stdin did not reach EOF before the deadline")
        chunk = os.read(fd, min(65536, MAX_MESSAGE + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_MESSAGE:
            raise ValueError("call stdin is too large")
    arguments = json.loads(payload.decode("utf-8"))
    if not isinstance(arguments, dict):
        raise ValueError("call stdin must be a JSON object")
    return arguments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "install", "health", "enable", "disable", "call", "audit"])
    parser.add_argument("operation", nargs="?")
    parser.add_argument("--tail", type=int, default=20, help="audit: number of recent records")
    parser.add_argument("--verify", action="store_true", help="audit: verify the hash chain")
    args = parser.parse_args()
    if args.command == "audit":
        # Reads only the metadata log; never Keychain or the socket.
        from .broker_audit import AuditLog, verify
        if args.verify:
            report = verify(audit_path())
            print(json.dumps(report))
            sys.exit(0 if report["ok"] else 1)
        for record in AuditLog(audit_path()).tail(max(1, args.tail)):
            print(json.dumps(record, ensure_ascii=False))
        return
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
            arguments = {} if args.command == "health" else _read_call_arguments()
            print(json.dumps(BrokerClient().call(operation, **arguments), ensure_ascii=False))
    except (BrokerError, OSError, ValueError, TypeError):
        print("Broker command failed; no direct Keychain fallback. Check service and operation arguments.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

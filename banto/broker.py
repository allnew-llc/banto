"""One macOS process owns Keychain operations; clients receive results only.

The Unix socket is a same-user boundary, not an app-signature sandbox. There is
deliberately no get-secret, arbitrary URL, shell, import, or environment API.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import fcntl as fcntl  # legacy module attribute
import inspect
import json
import os
from pathlib import Path
import plistlib as plistlib  # legacy module attribute
import socket as socket  # legacy module attribute
import socketserver as socketserver  # legacy module attribute
import struct as struct  # legacy module attribute
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from ._core import load_module

_transport = load_module("broker")

from .broker_client import (BrokerClient, BrokerError, MAX_MESSAGE as MAX_MESSAGE, check_private,
                            encode_message as encode_message, read_message as read_message, runtime_dir, socket_path)

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
    _transport.require_service(in_service=IN_SERVICE, directory=runtime_dir())


def observe_secret(value: str) -> None:
    _transport.observe_secret(value, observed=_observed)


NoRedirect = load_module("http").NoRedirect
request_fixed = load_module("http").request_fixed


def _audit(**meta) -> None:
    _transport.audit_metadata(meta, context=_audit_meta)


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
    _transport.check_rate_limit(_api_calls, rpm, now)


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
    try:
        result = request_fixed(provider, payload, key)
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


redact = _transport.redact


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
    return _transport.dispatch(operation, arguments, handler=_dispatch,
                               observed=_observed, redactor=redact, audit_context=_audit_meta)


def peer_uid(connection) -> int:
    return _transport.peer_uid(connection)


class Handler(_transport.Handler):
    # Resolve legacy globals per request, including monkeypatched private contexts.
    namespace = staticmethod(lambda: globals())


def _write_audit(operation: str, ok: bool, error: str | None, meta: dict) -> None:
    from .broker_audit import AuditLog
    AuditLog(audit_path()).append(operation, ok, error, meta)


class Server(_transport.Server):
    dispatch = staticmethod(dispatch)
    audit = staticmethod(_write_audit)  # Never emit request data or exception tracebacks.


def serve(path: Path | None = None) -> None:
    def service_state(value):
        global IN_SERVICE
        IN_SERVICE = value
    return _transport.serve(path or socket_path(), server_factory=Server,
                            handler_factory=Handler, set_service_state=service_state, path_checker=check_private)


def install() -> None:
    if sys.platform != "darwin":
        raise BrokerError("macos_required")
    # Preserve the legacy dependency check without any credential access.
    import mcp  # noqa: F401
    return _transport.install(directory=runtime_dir(), label=LABEL, module="banto.broker",
                              working_directory=Path(__file__).resolve().parent.parent,
                              client_factory=BrokerClient, runner=subprocess.run, path_checker=check_private)


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
            arguments = {} if args.command == "health" else json.load(sys.stdin)
            print(json.dumps(BrokerClient().call(operation, **arguments), ensure_ascii=False))
    except (BrokerError, OSError, ValueError, TypeError):
        print("Broker command failed; no direct Keychain fallback. Check service and operation arguments.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

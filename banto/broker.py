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
# Names handled by _dispatch before the MCP_OPERATIONS membership check.
# CLI help and unknown-operation errors read this; do not add secret-returning APIs.
DISPATCH_OPERATIONS = (
    "api_request",
    "health",
    "clienta_evaluation_azure_preflight",
    "clienta_evaluation_capabilities",
    "clienta_evaluation_generate",
    "clienta_evaluation_preflight",
    "clienta_evaluation_prepare",
    "clienta_material_capabilities",
    "clienta_material_provision",
    "register_asc",
    "register_x_oauth",
)
# Agent shells often leave stdin open and never write. Wait briefly so a real
# pipeline can deliver bytes, then continue with {} instead of blocking.
CALL_STDIN_POLL_SECONDS = 0.2


class BrokerUsageError(Exception):
    """CLI usage failure. The message must not contain argument values or secrets."""


def allowed_operations() -> tuple[str, ...]:
    return tuple(sorted(set(DISPATCH_OPERATIONS).union(MCP_OPERATIONS)))


def _json_kind(value: object) -> str:
    # bool is a subclass of int; classify it before numbers.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def parse_arguments_json(raw: str, *, source: str) -> dict:
    """Parse a JSON object. Errors omit the raw text so secrets cannot leak."""
    example = '{} or {"provider":"openai"}'
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise BrokerUsageError(
            f"Invalid JSON in {source}. Expected a JSON object such as {example}."
        ) from None
    if not isinstance(value, dict):
        kind = _json_kind(value)
        if kind == "null":
            phrase = "null"
        else:
            article = "an" if kind[0] in "aeiou" else "a"
            phrase = f"{article} {kind}"
        raise BrokerUsageError(
            f"Broker arguments from {source} must be a JSON object, not {phrase}."
        )
    return value


def _stdin_has_data(stdin, timeout: float) -> bool:
    """True when a read would not wait longer than timeout.

    In-memory streams used by tests have no file descriptor; their buffer is
    already complete. An idle pipe returns False.
    """
    try:
        fileno = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    readable, _, _ = select.select([fileno], [], [], timeout)
    return bool(readable)


def load_call_arguments(stdin, args_json: str | None) -> dict:
    """Resolve `broker call` arguments without blocking on an idle stdin.

    `--args` is explicit and does not touch stdin. A terminal, empty stdin, or
    a pipe that stays silent for CALL_STDIN_POLL_SECONDS means {}.
    """
    if args_json is not None:
        if not args_json.strip():
            raise BrokerUsageError(
                "`--args` must be a JSON object, for example --args '{}'."
            )
        return parse_arguments_json(args_json, source="--args")
    if stdin is None or stdin.isatty():
        return {}
    if not _stdin_has_data(stdin, CALL_STDIN_POLL_SECONDS):
        return {}
    try:
        raw = stdin.read()
    except UnicodeDecodeError:
        raise BrokerUsageError(
            "Broker arguments on stdin must be UTF-8 JSON text."
        ) from None
    if not raw or not str(raw).strip():
        return {}
    return parse_arguments_json(str(raw), source="stdin")


def reject_unknown_operation(operation: str) -> None:
    allowed = allowed_operations()
    if operation in allowed:
        return
    shown = operation.replace("\n", " ").replace("\r", " ")
    if len(shown) > 80:
        shown = shown[:79] + "…"
    listing = "\n".join(f"  {name}" for name in allowed)
    raise BrokerUsageError(
        f"Unknown broker operation {shown!r}.\nValid operations:\n{listing}"
    )


def call_usage_hint() -> str:
    return (
        "Usage: banto broker call <operation> [--args '<json>']\n"
        "       printf '%s\\n' '<json>' | banto broker call <operation>\n"
        "A terminal, empty stdin, or an idle pipe uses {}."
    )


def _call_epilog() -> str:
    listing = "\n".join(f"  {name}" for name in allowed_operations())
    return (
        "examples:\n"
        "  banto broker call banto_sync_status\n"
        "  banto broker call banto_register_key "
        "--args '{\"provider\":\"openai\"}'\n"
        "  printf '%s\\n' '{\"max_age_days\":7}' "
        "| banto broker call banto_sync_audit\n"
        "\n"
        "operations:\n"
        f"{listing}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="banto broker",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  banto broker health\n"
            "  banto broker call banto_budget_status\n"
            "  banto broker call banto_register_key "
            "--args '{\"provider\":\"openai\"}'\n"
            "  printf '%s\\n' '{\"provider\":\"openai\"}' "
            "| banto broker call banto_register_key"
        ),
    )
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)
    commands.add_parser("serve", help="Run the broker process (LaunchAgent entrypoint)")
    commands.add_parser("install", help="Install and start the user LaunchAgent")
    commands.add_parser(
        "health", help="Check broker readiness (does not read Keychain)",
    )
    commands.add_parser(
        "enable", help="Require the broker and refuse direct Keychain access",
    )
    commands.add_parser(
        "disable", help="Allow direct Keychain access again; restart MCP clients",
    )
    call = commands.add_parser(
        "call",
        help="Call one allowlisted operation",
        description=(
            "Call one allowlisted operation. Arguments are a JSON object passed\n"
            "with --args or piped on stdin. A terminal, empty stdin, or an idle\n"
            "pipe means {}, so operations with no arguments return immediately.\n"
            "Unknown names and invalid JSON fail before any broker call.\n"
            "--args is visible in the process list. Allowlisted operations do not\n"
            "take secret values and never return them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_call_epilog(),
    )
    call.add_argument("operation", help="Allowlisted operation name")
    call.add_argument(
        "--args",
        dest="args_json",
        metavar="JSON",
        help=(
            "JSON object of arguments. When set, stdin is not read. "
            "Example: --args '{\"provider\":\"openai\"}'"
        ),
    )
    return parser


def _run_command(args: argparse.Namespace) -> None:
    if args.command == "serve":
        # -m executes this file as __main__; use the canonical module so
        # Keychain's guard sees the same IN_SERVICE flag.
        from . import broker
        broker.serve()
        return
    if args.command == "install":
        install()
        return
    if args.command == "enable":
        BrokerClient().call("health")
        (runtime_dir() / "required").touch(mode=0o600)
        print('{"direct_banto_access_blocked": true}')
        return
    if args.command == "disable":
        (runtime_dir() / "required").unlink(missing_ok=True)
        print('{"direct_banto_access_blocked": false, "restart_mcp_clients": true}')
        return
    if args.command == "health":
        print(json.dumps(BrokerClient().call("health"), ensure_ascii=False))
        return
    if args.command == "call":
        # Reject before reading stdin so a typo cannot block on an idle pipe.
        reject_unknown_operation(args.operation)
        arguments = load_call_arguments(sys.stdin, args.args_json)
        result = BrokerClient().call(args.operation, **arguments)
        print(json.dumps(result, ensure_ascii=False))
        return
    raise BrokerUsageError(f"Unknown broker command {args.command!r}.")


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
    if operation == "clienta_evaluation_azure_preflight" and not arguments:
        from .clienta_evaluation import azure_preflight
        return azure_preflight()
    if operation == "clienta_evaluation_preflight" and not arguments:
        from .clienta_evaluation import preflight
        return preflight()
    if operation == "clienta_evaluation_capabilities" and not arguments:
        from .clienta_evaluation import capabilities
        return capabilities()
    if operation == "clienta_evaluation_prepare":
        if set(arguments) != {"candidate_sha", "limit_usd", "expires_at", "owner_confirm", "gemini_paid_tier_confirmed"}:
            raise BrokerError("clienta_evaluation_arguments_invalid")
        from .clienta_evaluation import prepare
        return prepare(**arguments)
    if operation == "clienta_evaluation_generate":
        if set(arguments) != {"provider", "payload", "request_id", "case_id", "candidate_sha"}:
            raise BrokerError("clienta_evaluation_arguments_invalid")
        from .clienta_evaluation import generate
        return generate(**arguments)
    if operation == "clienta_material_capabilities" and not arguments:
        from .clienta_material import capabilities
        return capabilities()
    if operation == "clienta_material_provision":
        if set(arguments) != {"environment", "purpose", "candidate_sha", "approved_region", "owner_confirm"}:
            raise BrokerError("clienta_material_arguments_invalid")
        from .clienta_material import provision
        return provision(**arguments)
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


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _run_command(args)
    except BrokerUsageError as error:
        print(f"banto broker: error: {error}", file=sys.stderr)
        if getattr(args, "command", None) == "call":
            print(call_usage_hint(), file=sys.stderr)
        sys.exit(2)
    except (BrokerError, OSError, ValueError, TypeError):
        print(
            "Broker command failed; no direct Keychain fallback. "
            "Check service and operation arguments.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

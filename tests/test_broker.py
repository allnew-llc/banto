"""Broker tests use fake secrets/backends only; never contact real providers."""
import asyncio
import io
import json
import os
from pathlib import Path
import pty
import re
import socket
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from banto import broker, broker_client, keychain
from banto.broker_client import BrokerClient, BrokerError


@pytest.fixture
def local_server(tmp_path):
    # macOS Unix socket paths have a 104-byte limit.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="banto-", dir="/tmp") as directory:
        path = Path(directory) / "b.sock"
        server = broker.Server(str(path), broker.Handler)
        os.chmod(path, 0o600)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server, BrokerClient(path)
        server.shutdown()
        server.server_close()
        thread.join()


def test_live_socket_health_and_same_process(local_server):
    server, client = local_server
    first = client.call("health")
    second = BrokerClient(client.path).call("health")
    assert first["pid"] == second["pid"] == os.getpid()
    assert first["status"] == "ready"


@pytest.mark.parametrize("operation", ["get", "get_secret", "store", "export", "exec", "__dict__"])
def test_secret_and_shell_operations_denied(local_server, operation):
    _, client = local_server
    with pytest.raises(BrokerError, match="operation_not_allowed"):
        client.call(operation)


def test_other_uid_denied(local_server, monkeypatch):
    _, client = local_server
    monkeypatch.setattr(broker, "peer_uid", lambda connection: os.getuid() + 1)
    with pytest.raises(BrokerError, match="peer_denied"):
        client.call("health")


def test_exception_does_not_leak(local_server):
    server, client = local_server
    def fail(*args):
        raise ValueError("FAKE_SECRET_MUST_NOT_ESCAPE")
    server.dispatch = fail
    with pytest.raises(BrokerError) as error:
        client.call("health")
    assert str(error.value) == "operation_failed"


def test_unsafe_socket_permissions_fail_before_connect(local_server):
    _, client = local_server
    os.chmod(client.path, 0o666)
    with pytest.raises(BrokerError, match="unsafe_broker_path"):
        client.call("health")


def test_missing_service_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(keychain, "_ctypes_get", Mock(side_effect=AssertionError("direct access")))
    with pytest.raises(BrokerError, match="broker_unavailable"):
        BrokerClient(tmp_path / "absent.sock").call("api_request")
    keychain._ctypes_get.assert_not_called()


def test_direct_access_guard_precedes_security_framework(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(broker, "IN_SERVICE", False)
    (tmp_path / "required").touch()
    with pytest.raises(BrokerError, match="direct_keychain_disabled"):
        keychain._ctypes_get("unused", "unused")
    with pytest.raises(BrokerError, match="direct_keychain_disabled"):
        keychain._ctypes_store("unused", "unused", "FAKE_VALUE")
    monkeypatch.setattr(broker, "IN_SERVICE", True)
    broker.require_service()


def test_protocol_rejects_oversize_and_non_objects(monkeypatch):
    monkeypatch.setattr(broker_client, "MAX_MESSAGE", 32)
    for data in [b"[]\n", b"x" * 33 + b"\n", b'{}']:
        with pytest.raises(BrokerError):
            broker_client.read_message(io.BytesIO(data))


def test_observed_secrets_redacted_and_released(monkeypatch):
    secret = "FAKE_SECRET_ONLY"
    def execute(*args):
        broker.observe_secret(secret)
        return {"nested": [{"message": "Bearer " + secret}], secret: secret}
    monkeypatch.setattr(broker, "_dispatch", execute)
    result = broker.dispatch("fixture", {})
    assert secret not in json.dumps(result)
    assert broker._observed.get() is None


@pytest.mark.parametrize("payload", [
    {"model": "fixture", "url": "https://example.com"},
    {"model": "fixture", "headers": {"Authorization": "fake"}},
    {"model": "fixture", "stream": True},
    {"model": "fixture", "tools": [{"type": "mcp"}]},
])
def test_api_rejects_unsafe_fields_before_key_read(payload, monkeypatch):
    get = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(keychain.KeychainStore, "get", get)
    with pytest.raises(BrokerError):
        broker.api_request("openai", payload)
    get.assert_not_called()


def test_api_fixed_endpoint_no_proxy_redaction(monkeypatch):
    from banto.sync.config import SyncConfig
    from banto.vault import SecureVault
    monkeypatch.setattr(SecureVault, "_try_auto_budget", lambda self: None)
    monkeypatch.setattr(SyncConfig, "load", lambda: Mock(keychain_service="fixture"))
    monkeypatch.setattr(keychain.KeychainStore, "get", lambda self, provider: "FAKE_ONLY")
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"output":"FAKE_\\u004fNLY"}'
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(broker.urllib.request, "build_opener", build)
    result = broker.api_request("openai", {"model": "fixture", "input": "hi"})
    assert result == {"output": "[REDACTED]"}
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert json.loads(request.data)["store"] is False
    assert build.call_args.args[0].proxies == {}
    assert isinstance(build.call_args.args[1], broker.NoRedirect)
    assert broker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None


def test_http_bridge_requires_token_and_configures_settings(monkeypatch, capsys):
    from banto import mcp_server, broker_mcp
    fake = Mock()
    monkeypatch.setattr(broker_mcp, "build_mcp", lambda: fake)
    monkeypatch.setattr(mcp_server, "mcp", fake)
    monkeypatch.setattr("sys.argv", ["banto-mcp", "--transport", "http", "--port", "8385"])
    monkeypatch.delenv("BANTO_MCP_PATH_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="requires"):
        mcp_server.main()
    fake.run.assert_not_called()
    token = "fixture-only-" + "x" * 32
    monkeypatch.setenv("BANTO_MCP_PATH_TOKEN", token)
    mcp_server.main()
    assert fake.settings.streamable_http_path == "/mcp-" + token
    assert fake.settings.host == "127.0.0.1"
    assert fake.settings.log_level == "WARNING"
    fake.run.assert_called_once_with(transport="streamable-http")
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


def test_budget_cannot_be_bypassed(monkeypatch):
    from banto.vault import SecureVault
    monkeypatch.setattr(SecureVault, "_try_auto_budget", lambda self: setattr(self, "_guard", object()))
    with pytest.raises(BrokerError, match="budget_enabled"):
        broker.api_request("openai", {"model": "fixture"})


def test_mcp_bridge_never_executes_original(monkeypatch):
    from banto.broker_mcp import bridge, build_mcp
    calls = []
    def call(self, operation, **arguments):
        calls.append((operation, arguments))
        return {"status": "fixture"}
    monkeypatch.setattr(BrokerClient, "call", call)
    async def operation(provider: str = "") -> dict:
        raise AssertionError("client executed operation")
    assert asyncio.run(bridge(operation)("openai")) == {"status": "fixture"}
    assert calls == [("operation", {"provider": "openai"})]
    names = {tool.name for tool in asyncio.run(build_mcp().list_tools())}
    assert set(broker.MCP_OPERATIONS) <= names
    assert "banto_api_request" in names


def test_registration_runs_in_service(monkeypatch):
    from banto import mcp_server
    from banto import register_popup
    called = Mock(return_value="http://127.0.0.1:12345")
    monkeypatch.setattr(register_popup, "serve_register_popup", called)
    result = broker.dispatch("banto_register_key", {"provider": "openai"})
    called.assert_called_once_with(provider_hint="openai", blocking=False)
    assert result["structuredContent"]["status"] == "popup_opened"


def test_cli_register_routes_and_export_denied(tmp_path, monkeypatch, capsys):
    from banto import broker_cli
    (tmp_path / "required").touch()
    monkeypatch.setattr(broker_cli, "runtime_dir", lambda: tmp_path)
    call = Mock(return_value={"status": "popup_opened"})
    monkeypatch.setattr(BrokerClient, "call", call)
    assert broker_cli.route_if_enabled(["register", "openai"])
    call.assert_called_once_with("banto_register_key", provider="openai")
    with pytest.raises(SystemExit, match="disabled"):
        broker_cli.route_if_enabled(["sync", "export"])


class _BoomStdin:
    """Stdin that fails if the CLI touches it. Used when input must be skipped."""

    def isatty(self):
        raise AssertionError("stdin was inspected")

    def fileno(self):
        raise AssertionError("stdin was inspected")

    def read(self, *args, **kwargs):
        raise AssertionError("stdin was read")


class _TtyStdin(io.StringIO):
    def isatty(self):
        return True

    def read(self, *args, **kwargs):
        raise AssertionError("terminal stdin must not be read")


def _broker_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "banto", "broker", *args]


def _run_holding_stdin(args: tuple[str, ...], timeout: float = 8):
    """Run with an open, empty stdin. communicate() would close it and hide hangs."""
    proc = subprocess.Popen(
        _broker_cmd(*args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        pytest.fail(
            f"{args} still running after {timeout}s\nstdout={out!r}\nstderr={err!r}"
        )
    out, err = proc.communicate()
    return subprocess.CompletedProcess(proc.args, code, out, err)


def _run_with_stdin_bytes(args: tuple[str, ...], payload: bytes, timeout: float = 8):
    """Deliver payload before the child starts so a short stdin poll cannot miss it."""
    read_fd, write_fd = os.pipe()
    try:
        if payload:
            os.write(write_fd, payload)
        os.close(write_fd)
        write_fd = -1
        return subprocess.run(
            _broker_cmd(*args),
            stdin=read_fd,
            capture_output=True,
            timeout=timeout,
        )
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


def _assert_broker_outcome(completed):
    """health is safe. A missing service fails closed; a local broker may answer."""
    assert completed.returncode in (0, 1)
    if completed.returncode == 0:
        payload = json.loads(completed.stdout)
        assert payload["status"] == "ready"
        assert "FAKE" not in completed.stdout.decode()
    else:
        assert b"no direct Keychain fallback" in completed.stderr
        assert b"Invalid JSON" not in completed.stderr


def test_cli_acceptance_follows_dispatch_rules():
    import inspect
    names, prefixes = broker.accepted_by_broker()
    source = inspect.getsource(broker._dispatch)
    for name in (
        "health", "api_request", "register_asc", "register_x_oauth",
        "kimaru_material_provision", "banto_sync_status", "banto_sealed_sign",
    ):
        assert name in names
        assert f'"{name}"' in source or name in broker.MCP_OPERATIONS
    assert "candidate_sha" not in names
    assert "pm_" in prefixes
    assert broker.operation_accepted("pm_status")
    assert broker.operation_accepted("pm_api")
    assert broker.operation_accepted("register_asc")
    assert not broker.operation_accepted("not_a_real_operation")
    for blocked in ("get", "get_secret", "store", "export", "exec"):
        assert not broker.operation_accepted(blocked)
        assert blocked not in names


def test_dispatch_parser_reads_exact_names_and_prefixes():
    source = """
def _dispatch(operation, arguments):
    if operation.startswith("pm_"):
        return {}
    if operation.startswith(("gemini_notebook_", "other_")):
        return {}
    if operation == "gemini_notebook_status":
        return {}
    if operation == "health":
        return {}
    if operation in ("register_asc", "register_x_oauth"):
        return {}
    if operation not in MCP_OPERATIONS:
        raise RuntimeError("nope")
    if set(arguments) != {"candidate_sha"}:
        return {}
"""
    names, prefixes = broker._acceptance_from_source(source)
    assert {"health", "register_asc", "register_x_oauth", "gemini_notebook_status"} <= names
    assert "banto_sync_status" in names
    assert "candidate_sha" not in names
    assert prefixes == {"pm_", "gemini_notebook_", "other_"}


def test_call_help_lists_usage_without_reading_stdin():
    overview = _run_holding_stdin(("--help",))
    assert overview.returncode == 0
    overview_text = overview.stdout.decode()
    assert "banto broker" in overview_text
    assert "call" in overview_text
    assert "--args" in overview_text
    assert "examples:" in overview_text

    call_help = _run_holding_stdin(("call", "--help"))
    assert call_help.returncode == 0
    call_text = call_help.stdout.decode()
    assert "--args" in call_text
    assert "stdin" in call_text
    assert "banto_sync_status" in call_text
    assert "examples:" in call_text
    assert "operations:" in call_text
    listed = set(re.findall(r"^  ([A-Za-z0-9_]+)$", call_text, re.M))
    assert "banto_sync_status" in listed
    assert "register_asc" in listed
    assert "pm_*" in call_text
    assert "banto broker call pm_status" in call_text
    assert "banto broker call pm_api" in call_text
    for blocked in ("get", "get_secret", "store", "export", "exec"):
        assert blocked not in listed

    module_help = subprocess.run(
        [sys.executable, "-m", "banto.broker", "call", "--help"],
        capture_output=True, text=True, timeout=8,
    )
    assert module_help.returncode == 0
    assert "banto_budget_status" in module_help.stdout
    assert "pm_*" in module_help.stdout
    assert "get_secret" not in module_help.stdout


def test_call_no_stdin_does_not_hang():
    held = _run_holding_stdin(("call", "health"))
    _assert_broker_outcome(held)
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        _broker_cmd("call", "health"),
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(slave)
    try:
        code = proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("banto broker call hung while stdin was a terminal")
    finally:
        os.close(master)
    out, err = proc.communicate()
    _assert_broker_outcome(subprocess.CompletedProcess(proc.args, code, out, err))


def test_call_empty_stdin_uses_empty_object(monkeypatch):
    call = Mock(return_value={"status": "ready", "pid": 1})
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    broker.main(["call", "health"])
    call.assert_called_once_with("health")
    call.reset_mock()
    monkeypatch.setattr(sys, "stdin", io.StringIO(" \n\t"))
    broker.main(["call", "banto_sync_status"])
    call.assert_called_once_with("banto_sync_status")
    completed = _run_with_stdin_bytes(("call", "health"), b"")
    _assert_broker_outcome(completed)


def test_call_tty_and_args_skip_stdin(monkeypatch, capsys):
    call = Mock(return_value={"status": "fixture"})
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))
    broker.main(["call", "banto_budget_status"])
    call.assert_called_once_with("banto_budget_status")
    assert "fixture" in capsys.readouterr().out

    call.reset_mock()
    monkeypatch.setattr(sys, "stdin", _BoomStdin())
    broker.main(["call", "banto_register_key", "--args", '{"provider":"openai"}'])
    call.assert_called_once_with("banto_register_key", provider="openai")
    held = _run_holding_stdin(("call", "health", "--args", "{}"))
    _assert_broker_outcome(held)


def test_call_stdin_json_still_works(monkeypatch, capsys):
    call = Mock(return_value={"status": "fixture"})
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"max_age_days":7}'))
    broker.main(["call", "banto_sync_audit"])
    call.assert_called_once_with("banto_sync_audit", max_age_days=7)
    assert "fixture" in capsys.readouterr().out
    completed = _run_with_stdin_bytes(("call", "health"), b"{}\n")
    _assert_broker_outcome(completed)


def test_call_rejects_invalid_json_without_echoing_it(monkeypatch, capsys):
    secret = "SUPER_SECRET_VALUE_SHOULD_NOT_APPEAR"
    call = Mock(side_effect=AssertionError("broker must not be called"))
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret))
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "health"])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "Invalid JSON in stdin" in err
    assert secret not in err
    assert "no direct Keychain fallback" not in err

    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "health", "--args", "{" + secret])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "Invalid JSON in --args" in err
    assert secret not in err

    completed = _run_with_stdin_bytes(("call", "health"), b"{")
    assert completed.returncode == 2
    assert b"Invalid JSON" in completed.stderr
    assert b"Usage:" in completed.stderr
    assert b"no direct Keychain fallback" not in completed.stderr
    non_object = _run_with_stdin_bytes(("call", "health"), b"[]")
    assert non_object.returncode == 2
    assert b"JSON object" in non_object.stderr
    assert b"[]" not in non_object.stderr


@pytest.mark.parametrize("payload,kind", [
    ("[]", "array"),
    ("null", "null"),
    ('"token"', "string"),
    ("1", "number"),
    ("true", "boolean"),
])
def test_call_rejects_non_object_arguments(payload, kind, monkeypatch, capsys):
    call = Mock(side_effect=AssertionError("broker must not be called"))
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "health"])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    expected = {
        "array": "not an array",
        "null": "not null",
        "string": "not a string",
        "number": "not a number",
        "boolean": "not a boolean",
    }[kind]
    assert expected in err
    # "null" is the type name in the error. Other payloads must not be echoed.
    if kind != "null":
        assert payload not in err
    call.assert_not_called()


def test_call_rejects_blank_args_flag(monkeypatch, capsys):
    call = Mock(side_effect=AssertionError("broker must not be called"))
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", _BoomStdin())
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "health", "--args", "   "])
    assert raised.value.code == 2
    assert "--args" in capsys.readouterr().err
    call.assert_not_called()


def test_call_forwards_pm_operations(monkeypatch, capsys):
    call = Mock(return_value={"status": "fixture"})
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))
    broker.main(["call", "pm_status"])
    call.assert_called_once_with("pm_status")
    assert "fixture" in capsys.readouterr().out

    call.reset_mock()
    monkeypatch.setattr(sys, "stdin", _BoomStdin())
    broker.main(["call", "pm_api", "--args", '{"path":"/status","limit":2}'])
    call.assert_called_once_with("pm_api", path="/status", limit=2)


def test_call_surfaces_safe_broker_error_and_hides_raw_text(monkeypatch, capsys):
    secret = "SUPER_SECRET_VALUE_SHOULD_NOT_APPEAR"
    monkeypatch.setattr(
        BrokerClient, "call", Mock(side_effect=BrokerError("operation_not_allowed")),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "pm_api", "--args", "{}"])
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "operation_not_allowed" in err
    assert "no direct Keychain fallback" in err

    monkeypatch.setattr(BrokerClient, "call", Mock(side_effect=BrokerError(secret)))
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "pm_status"])
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert secret not in err
    assert "operation_failed" in err


def test_call_unknown_operation_lists_valid_names_without_stdin(monkeypatch, capsys):
    call = Mock(side_effect=AssertionError("broker must not be called"))
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "stdin", _BoomStdin())
    with pytest.raises(SystemExit) as raised:
        broker.main(["call", "not_a_real_operation"])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "not_a_real_operation" in err
    assert "Valid operations:" in err
    assert "banto_sync_status" in err
    assert "api_request" in err
    assert "pm_*" in err
    assert "register_asc" in err
    assert "get_secret" not in err
    assert "Usage:" in err
    call.assert_not_called()

    completed = _run_holding_stdin(("call", "not_a_real_operation"))
    assert completed.returncode == 2
    text = completed.stderr.decode()
    assert "not_a_real_operation" in text
    assert "banto_budget_status" in text
    assert "pm_*" in text
    assert "no direct Keychain fallback" not in text


def test_call_idle_pipe_does_not_read(monkeypatch):
    call = Mock(return_value={"status": "ready"})
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(broker, "CALL_STDIN_POLL_SECONDS", 0.05)
    read_fd, write_fd = os.pipe()
    try:
        class IdlePipe:
            def isatty(self):
                return False

            def fileno(self):
                return read_fd

            def read(self, *args, **kwargs):
                raise AssertionError("idle stdin must not be read")

        monkeypatch.setattr(sys, "stdin", IdlePipe())
        broker.main(["call", "health"])
    finally:
        os.close(read_fd)
        os.close(write_fd)
    call.assert_called_once_with("health")


def test_install_starts_service_and_verifies_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(broker.sys, "platform", "darwin")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(broker.subprocess, "run", run)
    health = Mock(return_value={"status": "ready"})
    monkeypatch.setattr(BrokerClient, "call", health)
    broker.install()
    assert run.call_args_list[0].args[0][1] == "bootstrap"
    assert run.call_args_list[1].args[0][1] == "kickstart"
    health.assert_called_once_with("health")

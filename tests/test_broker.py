"""Broker tests use fake secrets/backends only; never contact real providers."""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
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

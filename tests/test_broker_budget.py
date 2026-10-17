"""Budgeted broker api_request, rate limiting and audit log (fake keys/HTTP only)."""
import io
import json
import os
import threading
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

from banto import broker, keychain
from banto.broker_audit import AuditLog, verify
from banto.broker_client import BrokerClient, BrokerError
from banto.guard import CostGuard

FAKE_KEY = "FAKE_KEY_ONLY_FOR_TESTS"
PRICING = {
    "fake-model": {"type": "per_token", "input_per_1k": 0.001, "output_per_1k": 0.002},
    "fake-image": {"type": "per_image", "fallback": 0.04},
}


def _usage(guard):
    return guard._load_usage()["entries"]


@pytest.fixture
def guard(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"monthly_limit_usd": 10, "pricing": PRICING,
                                  "broker": {"api_request_rpm": 0}}))
    g = CostGuard(config_path=str(config), caller="test", data_dir=str(tmp_path / "data"))
    from banto.vault import SecureVault
    monkeypatch.setattr(SecureVault, "_try_auto_budget", lambda self: setattr(self, "_guard", g))
    monkeypatch.setattr(broker, "_broker_settings", lambda: {"api_request_rpm": 0})
    return g


@pytest.fixture
def key_read(monkeypatch):
    from banto.sync.config import SyncConfig
    monkeypatch.setattr(SyncConfig, "load", lambda: Mock(keychain_service="fixture"))
    get = Mock(return_value=FAKE_KEY)
    monkeypatch.setattr(keychain.KeychainStore, "get", lambda self, provider: get(provider))
    return get


def _http(monkeypatch, body=None, error=None):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(body or {}).encode()
    opener = Mock()
    if error is not None:
        opener.open.side_effect = error
    else:
        opener.open.return_value = response
    monkeypatch.setattr(broker.urllib.request, "build_opener", Mock(return_value=opener))
    return opener


def _http_error(code):
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", code, "x", {}, io.BytesIO(b""))


OPENAI = {"model": "fake-model", "input": "hello", "max_output_tokens": 100}


def test_budget_requires_max_output_tokens_before_key_read(guard, key_read, monkeypatch):
    opener = _http(monkeypatch)
    for payload in ({"model": "fake-model", "input": "x"},
                    {"model": "fake-model", "input": "x", "max_output_tokens": 0},
                    {"model": "fake-model", "input": "x", "max_output_tokens": True}):
        with pytest.raises(BrokerError, match="budget_requires_max_output_tokens"):
            broker.api_request("openai", payload)
    with pytest.raises(BrokerError, match="budget_requires_max_tokens"):
        broker.api_request("anthropic", {"model": "fake-model", "messages": []})
    key_read.assert_not_called()
    opener.open.assert_not_called()
    assert _usage(guard) == []


@pytest.mark.parametrize("model", ["unknown-model", "fake-image", "_notice"])
def test_budget_rejects_unpriced_model(guard, key_read, monkeypatch, model):
    _http(monkeypatch)
    with pytest.raises(BrokerError, match="unpriced_model"):
        broker.api_request("openai", {**OPENAI, "model": model})
    key_read.assert_not_called()
    assert _usage(guard) == []


def test_budget_settles_from_reported_usage(guard, key_read, monkeypatch):
    opener = _http(monkeypatch, {"output": "ok", "usage": {"input_tokens": 10, "output_tokens": 20}})
    result = broker.api_request("openai", OPENAI)
    assert opener.open.called
    entries = _usage(guard)
    assert len(entries) == 1 and entries[0]["status"] == "settled"
    assert entries[0]["cost_usd"] == pytest.approx(10 / 1000 * 0.001 + 20 / 1000 * 0.002)
    assert entries[0]["hold_cost_usd"] > entries[0]["cost_usd"]
    assert result["banto_budget"]["status"] == "settled"
    assert result["banto_budget"]["settled_usd"] == pytest.approx(entries[0]["cost_usd"])
    assert FAKE_KEY not in json.dumps(result)


def test_budget_reservation_uses_worst_case(guard, key_read, monkeypatch):
    _http(monkeypatch, error=OSError("timeout"))
    with pytest.raises(BrokerError):
        broker.api_request("openai", OPENAI)
    hold = _usage(guard)[0]
    input_bound = len(json.dumps(OPENAI, ensure_ascii=False).encode())
    assert hold["params"]["input_tokens"] == input_bound
    assert hold["params"]["output_tokens"] == 100


def test_budget_exceeded_blocks_before_key_and_network(guard, key_read, monkeypatch):
    opener = _http(monkeypatch)
    with pytest.raises(BrokerError, match="budget_exceeded"):
        broker.api_request("openai", {**OPENAI, "max_output_tokens": 10_000_000})
    key_read.assert_not_called()
    opener.open.assert_not_called()


def test_budget_provider_and_model_limits_apply(guard, key_read, monkeypatch):
    guard.model_limits["fake-model"] = 0.0001
    _http(monkeypatch)
    with pytest.raises(BrokerError, match="budget_exceeded"):
        broker.api_request("openai", OPENAI)
    key_read.assert_not_called()


def test_budget_definite_client_error_voids_hold(guard, key_read, monkeypatch):
    _http(monkeypatch, error=_http_error(400))
    assert broker.api_request("openai", OPENAI) == {"error": "upstream_http_error", "status": 400}
    assert _usage(guard) == []


@pytest.mark.parametrize("error", [_http_error(500), OSError("timeout"), ValueError("bad json")])
def test_budget_unknown_outcome_keeps_durable_hold(guard, key_read, monkeypatch, error):
    _http(monkeypatch, error=error)
    if isinstance(error, urllib.error.HTTPError):
        assert broker.api_request("openai", OPENAI)["status"] == 500
    else:
        with pytest.raises(BrokerError, match="outcome_unknown"):
            broker.api_request("openai", OPENAI)
    entries = _usage(guard)
    assert len(entries) == 1
    assert entries[0]["status"] == "hold" and entries[0]["durable"] is True


def test_budget_missing_usage_keeps_hold(guard, key_read, monkeypatch):
    _http(monkeypatch, {"output": "ok"})
    result = broker.api_request("openai", OPENAI)
    assert result["banto_budget"]["status"] == "kept_usage_missing"
    assert _usage(guard)[0]["status"] == "hold"


def test_budget_key_unavailable_voids_hold(guard, key_read, monkeypatch):
    key_read.return_value = None
    opener = _http(monkeypatch)
    with pytest.raises(BrokerError, match="key_unavailable"):
        broker.api_request("openai", OPENAI)
    opener.open.assert_not_called()
    assert _usage(guard) == []


def test_budget_anthropic_counts_cache_tokens(guard, key_read, monkeypatch):
    _http(monkeypatch, {"content": [], "usage": {
        "input_tokens": 5, "cache_creation_input_tokens": 7, "cache_read_input_tokens": 3,
        "output_tokens": 11}})
    result = broker.api_request("anthropic", {"model": "fake-model", "messages": [], "max_tokens": 50})
    entry = _usage(guard)[0]
    assert entry["params"]["input_tokens"] == 15
    assert entry["params"]["output_tokens"] == 11
    assert result["banto_budget"]["status"] == "settled"


def test_budget_disabled_path_unchanged(key_read, monkeypatch):
    from banto.vault import SecureVault
    monkeypatch.setattr(SecureVault, "_try_auto_budget", lambda self: None)
    monkeypatch.setattr(broker, "_broker_settings", lambda: {})
    _http(monkeypatch, {"output": "ok"})
    result = broker.api_request("openai", {"model": "anything", "input": "x"})
    assert result == {"output": "ok"}  # no max tokens or pricing needed


# ── rate limit ───────────────────────────────────────────────────


def test_rate_limit_sliding_window(monkeypatch):
    monkeypatch.setattr(broker, "_broker_settings", lambda: {"api_request_rpm": 2})
    broker._check_rate_limit(now=100.0)
    broker._check_rate_limit(now=101.0)
    with pytest.raises(BrokerError, match="rate_limited"):
        broker._check_rate_limit(now=110.0)
    broker._check_rate_limit(now=160.5)  # first call left the window


def test_rate_limit_default_and_disable(monkeypatch):
    monkeypatch.setattr(broker, "_broker_settings", lambda: {})
    for i in range(broker.DEFAULT_API_REQUEST_RPM):
        broker._check_rate_limit(now=float(i) / 100)
    with pytest.raises(BrokerError, match="rate_limited"):
        broker._check_rate_limit(now=1.0)
    broker._api_calls.clear()
    monkeypatch.setattr(broker, "_broker_settings", lambda: {"api_request_rpm": 0})
    for i in range(500):
        broker._check_rate_limit(now=1.0)


@pytest.mark.parametrize("value", [-1, "30", 1.5, True])
def test_rate_limit_invalid_config_fails_closed(monkeypatch, value):
    monkeypatch.setattr(broker, "_broker_settings", lambda: {"api_request_rpm": value})
    with pytest.raises(BrokerError, match="broker_config_invalid"):
        broker._check_rate_limit(now=1.0)


def test_rate_limited_call_releases_hold_and_skips_key(guard, key_read, monkeypatch):
    monkeypatch.setattr(broker, "_broker_settings", lambda: {"api_request_rpm": 1})
    _http(monkeypatch, {"usage": {"input_tokens": 1, "output_tokens": 1}})
    broker.api_request("openai", OPENAI)
    key_read.reset_mock()
    with pytest.raises(BrokerError, match="rate_limited"):
        broker.api_request("openai", OPENAI)
    key_read.assert_not_called()
    assert [e["status"] for e in _usage(guard)] == ["settled"]


def test_broker_settings_reads_config_section(tmp_path, monkeypatch):
    from banto import guard as guard_module
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"monthly_limit_usd": 0, "broker": {"api_request_rpm": 7}}))
    monkeypatch.setattr(guard_module, "_resolve_config_path", lambda path: config)
    assert broker._broker_settings() == {"api_request_rpm": 7}
    config.write_text("not json")
    assert broker._broker_settings() == {}


# ── audit log ────────────────────────────────────────────────────


def test_audit_chain_verifies_and_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(3):
        log.append("api_request", True, None, {"provider": "openai", "model": f"m{i}"})
    assert verify(log.path) == {"ok": True, "entries": 3, "broken_at": None, "reason": None}
    assert oct(log.path.stat().st_mode & 0o777) == "0o600"
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join([lines[0], lines[1].replace("m1", "mX"), lines[2]]) + "\n")
    assert verify(log.path)["reason"] == "hash_mismatch"
    log.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert verify(log.path)["reason"] == "chain_broken"


def test_audit_never_records_values_or_unknown_keys(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    record = log.append("banto_register_key", False, "key_unavailable; details " + FAKE_KEY, {
        "argument_keys": ["provider", "payload"], "payload": {"input": FAKE_KEY},
        "api_key": FAKE_KEY, "provider": "openai", "model": {"nested": FAKE_KEY}})
    text = log.path.read_text()
    assert FAKE_KEY not in text
    assert record["meta"] == {"argument_keys": ["payload", "provider"], "provider": "openai"}
    assert record["error"] == "key_unavailable"


def test_audit_rotation_keeps_chain(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=300)
    for i in range(6):
        log.append("op", True, None, {"model": "m" * 40})
    assert (tmp_path / "audit.jsonl.1").exists()
    report = verify(log.path)
    assert report["ok"] is True and report["entries"] >= 2
    assert log.tail(1)[0]["seq"] == 5


@pytest.fixture
def live(tmp_path):
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


def test_live_requests_are_audited_without_values(live, monkeypatch):
    server, client = live
    with pytest.raises(BrokerError, match="operation_not_allowed"):
        client.call("get_secret", name=FAKE_KEY)
    client.call("health")  # health probes are not audited
    records = AuditLog(broker.audit_path()).tail()
    assert [r["operation"] for r in records] == ["get_secret"]
    assert records[0]["ok"] is False and records[0]["error"] == "operation_not_allowed"
    assert records[0]["meta"]["argument_keys"] == ["name"]
    assert FAKE_KEY not in broker.audit_path().read_text()
    assert verify(broker.audit_path())["ok"]


def test_live_api_request_audit_has_budget_metadata(live, guard, key_read, monkeypatch):
    _, client = live
    _http(monkeypatch, {"usage": {"input_tokens": 10, "output_tokens": 20}})
    client.call("api_request", provider="openai", payload=OPENAI)
    record = AuditLog(broker.audit_path()).tail(1)[0]
    assert record["operation"] == "api_request"
    meta = record["meta"]
    assert meta["provider"] == "openai" and meta["model"] == "fake-model"
    assert meta["budget"] == "settled" and meta["status"] == 200
    assert meta["settled_usd"] > 0 and meta["estimated_usd"] >= meta["settled_usd"]
    assert "hello" not in broker.audit_path().read_text()


def test_audit_failure_does_not_break_reply(live):
    server, client = live
    server.audit = Mock(side_effect=OSError("disk full"))
    with pytest.raises(BrokerError, match="operation_not_allowed"):
        client.call("nope")


def test_broker_audit_cli_verify(monkeypatch, capsys):
    AuditLog(broker.audit_path()).append("op", True)
    monkeypatch.setattr("sys.argv", ["broker", "audit", "--verify"])
    with pytest.raises(SystemExit) as exc:
        broker.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    broker.audit_path().write_text('{"broken": true}\n')
    with pytest.raises(SystemExit) as exc:
        broker.main()
    assert exc.value.code == 1
    monkeypatch.setattr("sys.argv", ["broker", "audit", "--tail", "5"])
    broker.audit_path().unlink()
    broker.main()

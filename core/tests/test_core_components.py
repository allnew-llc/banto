# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Core-only namespace tests; native Security APIs are never invoked.

All values are synthetic. Framework lookup/loading is blocked before importing
core modules, and existing native handles are replaced with unavailable mocks.
"""

import ctypes
import ctypes.util
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest


CORE_ROOT = Path(__file__).resolve().parents[1]
DUMMY_SECRET = "DUMMY_CORE_SECRET_NOT_A_CREDENTIAL"


@pytest.fixture
def components(monkeypatch):
    monkeypatch.setattr(ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(ctypes.cdll, "LoadLibrary", Mock(
        side_effect=AssertionError("native framework loading forbidden in unit tests")))
    modules = {name: importlib.import_module("banto_core." + name) for name in (
        "broker_client", "broker_audit", "ecdsa_p256", "keychain", "sealed_signer")}
    keychain = modules["keychain"]
    sealed = modules["sealed_signer"]
    monkeypatch.setattr(keychain, "_SECURITY_LIB", None)
    monkeypatch.setattr(sealed, "_SEC", None)
    monkeypatch.setattr(sealed, "_CF", None)
    monkeypatch.setattr(keychain, "_DEFAULT_HOOKS",
                        (keychain._unbound_guard, keychain._discard_observation))
    key_token = keychain._ACTIVE_HOOKS.set(None)
    sealed_token = sealed._ACTIVE_PREFIX.set(None)
    try:
        yield modules
    finally:
        sealed._ACTIVE_PREFIX.reset(sealed_token)
        keychain._ACTIVE_HOOKS.reset(key_token)


def test_core_imports_without_banto_or_native_frameworks(tmp_path):
    # Isolated stdlib interpreter: do not inherit application imports or env.
    script = """
import ctypes, ctypes.util, importlib, sys
sys.path.insert(0, sys.argv[1])
ctypes.util.find_library = lambda name: None
def forbidden(*args, **kwargs):
    raise AssertionError('native framework loading forbidden')
ctypes.cdll.LoadLibrary = forbidden
for name in ('broker_client', 'broker_audit', 'ecdsa_p256', 'keychain', 'sealed_signer'):
    importlib.import_module('banto_core.' + name)
assert not any(name == 'banto' or name.startswith('banto.') for name in sys.modules)
print('core_only_imports_ok')
"""
    process = subprocess.run(
        [sys.executable, "-B", "-I", "-S", "-c", script, str(CORE_ROOT)],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=15, check=False)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "core_only_imports_ok"


def test_custom_runtime_paths_do_not_change_defaults(components, tmp_path, monkeypatch):
    client = components["broker_client"]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    default = tmp_path / "home/Library/Application Support/banto/broker"
    first, second = tmp_path / "first", tmp_path / "second"
    assert client.runtime_dir() == default
    assert client.socket_path() == default / "broker.sock"
    assert client.runtime_dir(first) == first
    assert client.socket_path(first) == first / "broker.sock"
    assert client.BrokerClient(runtime_directory=second).path == second / "broker.sock"
    explicit = tmp_path / "explicit.sock"
    assert client.BrokerClient(explicit, runtime_directory=second).path == explicit
    assert client.BrokerClient().path == default / "broker.sock"
    assert client.runtime_dir() == default


def test_keychain_injected_guards_and_observers_are_independent(components):
    keychain = components["keychain"]
    first_guard, second_guard = Mock(), Mock()
    first_observed, second_observed = [], []
    first = keychain.KeychainStore(account="fixture", service_prefix="factory-a",
        require_service=first_guard, observe_secret=first_observed.append)
    second = keychain.KeychainStore(account="fixture", service_prefix="factory-b",
        require_service=second_guard, observe_secret=second_observed.append)
    # No framework exists in this fixture; only guard and observation are exercised.
    assert first.store("provider", DUMMY_SECRET) is False
    assert first_observed == [DUMMY_SECRET] and second_observed == []
    assert second.store("provider", "SECOND_DUMMY") is False
    assert second_observed == ["SECOND_DUMMY"] and first_observed == [DUMMY_SECRET]
    first_guard.assert_called_once_with()
    second_guard.assert_called_once_with()
    assert keychain._ACTIVE_HOOKS.get() is None
    default = keychain.KeychainStore(account="fixture")
    with pytest.raises(RuntimeError, match="injected broker guard"):
        default.store("provider", DUMMY_SECRET)
    assert keychain._ACTIVE_HOOKS.get() is None


def test_keychain_hook_exception_restores_fail_closed_default(components):
    keychain = components["keychain"]
    observer = Mock()
    denied = keychain.KeychainStore(account="fixture",
        require_service=Mock(side_effect=RuntimeError("fixture_denied")),
        observe_secret=observer)
    with pytest.raises(RuntimeError, match="fixture_denied"):
        denied.store("provider", DUMMY_SECRET)
    observer.assert_not_called()
    assert keychain._ACTIVE_HOOKS.get() is None
    with pytest.raises(RuntimeError, match="injected broker guard"):
        keychain._ctypes_store("fixture-service", "fixture", DUMMY_SECRET)


def test_keychain_service_prefixes_do_not_change_banto_default(components):
    keychain = components["keychain"]
    before = keychain.KeychainStore.DEFAULT_PREFIX
    assert before == "banto"
    first = keychain.KeychainStore(service_prefix="allnew-factory-a", account="fixture")
    second = keychain.KeychainStore(service_prefix="allnew-factory-b", account="fixture")
    assert first._service_name("Provider") == "allnew-factory-a-provider"
    assert second._service_name("Provider") == "allnew-factory-b-provider"
    assert keychain.KeychainStore(account="fixture")._service_name("Provider") == "banto-provider"
    assert keychain.KeychainStore.DEFAULT_PREFIX == before


def test_sealed_nested_namespaces_restore_outer_and_default(components, monkeypatch):
    sealed = components["sealed_signer"]
    first = sealed.SealedSigner(tag_prefix=b"com.fixture.first.")
    second = sealed.SealedSigner(tag_prefix=b"com.fixture.second.")
    observed = []

    def fake_sign(key_id, payload):
        tag = sealed._tag(key_id)
        observed.append(tag)
        if payload == b"outer":
            assert second.sign(key_id, b"inner") == b"com.fixture.second.key"
            assert sealed._tag(key_id) == tag
        return tag

    monkeypatch.setattr(sealed, "sign", fake_sign)
    assert first.sign("key", b"outer") == b"com.fixture.first.key"
    assert observed == [b"com.fixture.first.key", b"com.fixture.second.key"]
    assert sealed._ACTIVE_PREFIX.get() is None
    assert sealed._tag("key") == sealed.TAG_PREFIX + b"key"
    assert sealed.TAG_PREFIX == b"com.allnew.banto.sealed."


def test_sealed_exception_restores_default_namespace(components, monkeypatch):
    sealed = components["sealed_signer"]
    signer = sealed.SealedSigner(tag_prefix=b"com.fixture.error.")

    def fail(key_id, _payload):
        assert sealed._tag(key_id) == b"com.fixture.error.key"
        raise RuntimeError("synthetic_sign_failure")

    monkeypatch.setattr(sealed, "sign", fail)
    with pytest.raises(RuntimeError, match="synthetic_sign_failure"):
        signer.sign("key", b"dummy-payload")
    assert sealed._ACTIVE_PREFIX.get() is None
    assert sealed._tag("key") == sealed.TAG_PREFIX + b"key"


def test_sealed_threads_do_not_share_namespace(components, monkeypatch):
    sealed = components["sealed_signer"]
    barrier = threading.Barrier(2)
    results, errors = {}, []

    def fake_sign(key_id, _payload):
        before = sealed._tag(key_id)
        barrier.wait(timeout=5)
        after = sealed._tag(key_id)
        assert before == after
        return after

    def worker(name, prefix):
        try:
            signer = sealed.SealedSigner(tag_prefix=prefix)
            result = signer.sign("key", b"dummy")
            results[name] = (result, sealed._ACTIVE_PREFIX.get())
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(sealed, "sign", fake_sign)
    threads = [threading.Thread(target=worker, args=(name, prefix), daemon=True)
               for name, prefix in (("first", b"com.fixture.first."),
                                    ("second", b"com.fixture.second."))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=6)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == {"first": (b"com.fixture.first.key", None),
                       "second": (b"com.fixture.second.key", None)}
    assert sealed._ACTIVE_PREFIX.get() is None
    assert sealed._tag("key") == sealed.TAG_PREFIX + b"key"


@pytest.mark.parametrize("prefix", [None, "com.fixture.", bytearray(b"com.fixture."),
    b"", b".", b"missing-dot", b"bad/prefix.", b"bad prefix.", b"\xff.", b"a" * 129 + b"."])
def test_sealed_rejects_invalid_prefix_without_native_calls(components, prefix):
    sealed = components["sealed_signer"]
    with pytest.raises(ValueError, match="tag_prefix"):
        sealed.SealedSigner(tag_prefix=prefix)
    assert sealed._ACTIVE_PREFIX.get() is None
    assert sealed.TAG_PREFIX == b"com.allnew.banto.sealed."


def test_ecdsa_parsing_and_malformed_signatures_need_no_native_crypto(components):
    ecdsa = components["ecdsa_p256"]
    point = ecdsa._G  # Public standard generator, not private key material.
    raw = b"\x04" + point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")
    assert ecdsa.public_key_from_x963(raw) == point
    assert ecdsa._on_curve(point)
    assert not ecdsa._on_curve((point[0] + ecdsa._P, point[1]))
    for signature in (b"", b"not-der", bytes.fromhex("3006020401020304")):
        assert ecdsa.verify(raw, b"dummy-payload", signature) is False
    with pytest.raises(ValueError):
        ecdsa.public_key_from_x963(b"\x04\x00")


def test_audit_keeps_only_allowed_metadata_and_no_dummy_secret(components, tmp_path):
    audit = components["broker_audit"]
    log = audit.AuditLog(tmp_path / "fixture-audit.jsonl")
    record = log.append("fixture_operation", False, "fixture_denied; " + DUMMY_SECRET, {
        "provider": "fixture", "argument_keys": ["provider", "payload"],
        "prompt": DUMMY_SECRET, "secret": DUMMY_SECRET, "payload": {"value": DUMMY_SECRET},
        "model": {"unexpected_nested_value": DUMMY_SECRET}, "input_tokens": 1})
    assert record["meta"] == {"provider": "fixture", "argument_keys": ["payload", "provider"],
                              "input_tokens": 1}
    assert set(record["meta"]) <= audit.ALLOWED_META
    assert record["error"] == "fixture_denied"
    assert DUMMY_SECRET not in json.dumps(record)
    assert DUMMY_SECRET not in log.path.read_text()
    assert log.tail(1) == [record]
    assert audit.verify(log.path) == {"ok": True, "entries": 1, "broken_at": None, "reason": None}

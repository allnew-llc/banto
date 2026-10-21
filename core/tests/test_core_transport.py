"""Real fixture sockets, fake operation table. No Keychain or providers."""
import io
import json
import os
from contextvars import ContextVar
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from banto_core import broker, broker_client
from banto_core.broker_audit import AuditLog
from banto_core.broker_client import BrokerClient, BrokerError


@pytest.fixture
def endpoint():
    with tempfile.TemporaryDirectory(prefix="banto-", dir="/tmp") as directory:
        path = Path(directory) / "b.sock"
        audit = AuditLog(Path(directory) / "audit.jsonl")

        def execute(operation, arguments):
            if operation not in {"fixture", "fail"}:
                raise BrokerError("operation_not_allowed")
            broker.observe_secret("fixture-only-value")
            if operation == "fail":
                raise BrokerError("failed fixture-only-value")
            return {"value": "fixture-only-value", "status": "ready"}

        server = broker.Server(
            str(path), dispatcher=lambda op, args: broker.dispatch(op, args, handler=execute),
            audit=audit.append,
        )
        os.chmod(path, 0o600)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield BrokerClient(path), audit
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


def test_socket_redaction_and_metadata_only_audit(endpoint):
    client, audit = endpoint
    result = client.call("fixture", value="fixture-only-value")
    assert result == {"value": "[REDACTED]", "status": "ready"}
    assert "fixture-only-value" not in audit.path.read_text()
    assert audit.tail()[0]["meta"]["argument_keys"] == ["value"]


def test_different_peer_uid_rejected(endpoint):
    client, _ = endpoint
    with patch.object(broker, "peer_uid", return_value=os.getuid() + 1):
        with pytest.raises(BrokerError, match="peer_denied"):
            client.call("fixture")


def test_unregistered_operation_rejected(endpoint):
    client, _ = endpoint
    with pytest.raises(BrokerError, match="operation_not_allowed"):
        client.call("get_secret")


def test_oversize_request_and_reply_rejected():
    with patch.object(broker_client, "MAX_MESSAGE", 40):
        with pytest.raises(BrokerError, match="invalid_message"):
            broker_client.read_message(io.BytesIO(b"x" * 41 + b"\n"))
        with pytest.raises(BrokerError, match="message_too_large"):
            broker_client.encode_message({"output": "x" * 40})


def test_dispatch_context_cleared_after_error():
    def fail(*args):
        broker.observe_secret("fixture-only-value")
        raise ValueError("fixture")

    with pytest.raises(ValueError):
        broker.dispatch("fixture", {}, handler=fail)
    assert broker._observed.get() is None
    assert json.dumps(broker.dispatch("fixture", {}, handler=lambda *args: {})) == "{}"


def test_observed_secret_in_broker_error_is_redacted():
    def fail(*args):
        broker.observe_secret("fixture-only-value")
        raise BrokerError("fixture; fixture-only-value")

    with pytest.raises(BrokerError) as error:
        broker.dispatch("fixture", {}, handler=fail)
    assert str(error.value) == "fixture; [REDACTED]"
    assert broker._observed.get() is None


def test_socket_error_and_metadata_are_redacted(endpoint):
    client, audit = endpoint
    with pytest.raises(BrokerError, match="failed ") as error:
        client.call("fail", **{"fixture-only-value": "dummy"})
    assert "fixture-only-value" not in str(error.value)
    assert "fixture-only-value" not in audit.path.read_text()
    assert audit.tail()[0]["meta"]["argument_keys"] == ["[REDACTED]"]


def test_tuple_secrets_are_redacted_without_changing_tuple_types():
    dummy = "fixture-tuple-secret"

    def execute(*args):
        broker.observe_secret(dummy)
        return {"nested": (dummy, [({"value": dummy},)])}

    result = broker.dispatch("fixture", {}, handler=execute)
    assert result == {"nested": ("[REDACTED]", [({"value": "[REDACTED]"},)])}
    assert isinstance(result["nested"], tuple)
    assert isinstance(result["nested"][1][0], tuple)
    encoded = broker_client.encode_message(result)
    assert dummy.encode() not in encoded
    assert json.loads(encoded)["nested"][0] == "[REDACTED]"
    assert broker._observed.get() is None


@pytest.mark.parametrize("failure", ["redactor_raises", "cyclic_metadata"])
def test_metadata_redaction_failure_drops_audit_and_clears_secrets(tmp_path, failure):
    dummy = "fixture-audit-cleanup-secret"
    audit = AuditLog(tmp_path / "audit.jsonl")
    retained_sets, metadata = [], []
    previous = broker._observed.get()

    def execute(*args):
        broker.observe_secret(dummy)
        retained_sets.append(broker._observed.get())
        metadata.append(broker._audit_meta.get())
        debug = {}
        if failure == "cyclic_metadata":
            debug["self"] = debug
        broker.audit_metadata({"provider": dummy, "ignored_debug": debug})
        return {"safe": True}

    def redactor(value, values):
        if failure == "redactor_raises" and metadata and value is metadata[0]:
            raise RuntimeError("fixture_metadata_redaction_failed")
        return broker.redact(value, values)

    handler = object.__new__(broker.Handler)
    handler.connection = Mock()
    handler.rfile = io.BytesIO(b'{"operation":"fixture","arguments":{}}\n')
    handler.wfile = io.BytesIO()
    handler.server = SimpleNamespace(
        dispatch=lambda op, args: broker.dispatch(op, args, handler=execute, redactor=redactor),
        audit=audit.append)
    with patch.object(broker, "peer_uid", return_value=os.getuid()):
        handler.handle()

    assert json.loads(handler.wfile.getvalue()) == {"ok": False, "error": "operation_failed"}
    assert metadata == [{}]
    assert retained_sets == [set()]
    assert broker._observed.get() is previous
    assert audit.tail()[0]["meta"] == {}
    assert dummy not in audit.path.read_text()
    assert dummy.encode() not in handler.wfile.getvalue()


def test_custom_contexts_restore_outer_observation_when_metadata_redaction_fails():
    dummy = "fixture-custom-context-secret"
    observed = ContextVar("fixture_observed", default=None)
    audit_context = ContextVar("fixture_audit", default=None)
    outer = {"outer-fixture"}
    outer_token = observed.set(outer)
    metadata = {"provider": dummy}
    audit_token = audit_context.set(metadata)
    retained_sets = []

    def execute(*args):
        broker.observe_secret(dummy, observed=observed)
        retained_sets.append(observed.get())
        return {"safe": True}

    def redactor(value, values):
        if value is metadata:
            raise ValueError("fixture_metadata_redaction_failed")
        return broker.redact(value, values)

    try:
        with pytest.raises(ValueError, match="fixture_metadata_redaction_failed"):
            broker.dispatch("fixture", {}, handler=execute, observed=observed,
                            audit_context=audit_context, redactor=redactor)
        assert metadata == {}
        assert retained_sets == [set()]
        assert observed.get() is outer
        assert outer == {"outer-fixture"}
    finally:
        audit_context.reset(audit_token)
        observed.reset(outer_token)

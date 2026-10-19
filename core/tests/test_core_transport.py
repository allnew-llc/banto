"""Real fixture sockets, fake operation table. No Keychain or providers."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

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

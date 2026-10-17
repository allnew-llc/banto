"""Shared test isolation: never touch the real broker runtime directory."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_broker_runtime(tmp_path_factory, monkeypatch):
    from banto import broker
    runtime = tmp_path_factory.mktemp("broker-runtime")
    monkeypatch.setattr(broker, "runtime_dir", lambda: runtime)
    broker._api_calls.clear()
    yield
    broker._api_calls.clear()

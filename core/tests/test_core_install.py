"""LaunchAgent and rate fixtures; launchctl is replaced before use."""
from collections import deque
from pathlib import Path
import plistlib
from unittest.mock import Mock

import pytest
from banto_core import broker
from banto_core.broker_client import BrokerError


def test_custom_launch_agent_namespace_is_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(broker.sys, "platform", "darwin")
    runner = Mock(return_value=Mock(returncode=0))
    client = Mock()
    factory = Mock(return_value=client)
    runtime = tmp_path / "factory-runtime"
    broker.install(directory=runtime, label="work.allnew.factory.broker",
                   module="factory_broker", working_directory=tmp_path,
                   client_factory=factory, runner=runner)
    plist = tmp_path / "Library/LaunchAgents/work.allnew.factory.broker.plist"
    spec = plistlib.loads(plist.read_bytes())
    assert spec["Label"] == "work.allnew.factory.broker"
    assert spec["ProgramArguments"][-3:] == ["-m", "factory_broker", "serve"]
    assert spec["WorkingDirectory"] == str(tmp_path)
    assert plist.stat().st_mode & 0o777 == 0o600
    assert runner.call_count == 2
    client.call.assert_called_once_with("health")
    with pytest.raises(BrokerError, match="launch_agent_already_exists"):
        broker.install(directory=runtime, label="work.allnew.factory.broker",
                       module="factory_broker", working_directory=tmp_path,
                       client_factory=factory, runner=runner)
    assert runner.call_count == 2


def test_rate_limit_window_and_disabled_mode():
    calls = deque()
    broker.check_rate_limit(calls, 1, 100.0)
    with pytest.raises(BrokerError, match="rate_limited"):
        broker.check_rate_limit(calls, 1, 101.0)
    broker.check_rate_limit(calls, 1, 160.0)
    assert list(calls) == [160.0]
    broker.check_rate_limit(calls, 0, 161.0)
    assert list(calls) == [160.0]

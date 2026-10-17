"""5.3.0 CLI fixes: list/profile/check without budget, register-x-oauth pointer."""
import json
import sys
from unittest.mock import Mock

import pytest

import banto.__main__ as cli
from banto import guard as guard_module
from banto import keychain
from banto.guard import BUNDLED_CONFIG


@pytest.fixture
def home(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(guard_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli, "CONFIG_DIR", config_dir)
    return config_dir


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["banto", *argv])
    cli.main()


def _budget_config(home, **extra):
    home.mkdir(parents=True, exist_ok=True)
    config = json.loads(BUNDLED_CONFIG.read_text())
    config.update(monthly_limit_usd=10, **extra)
    (home / "config.json").write_text(json.dumps(config))


def test_list_without_budget_shows_stored_keys(home, monkeypatch, capsys):
    from banto.sync.config import SecretEntry, SyncConfig
    sync = SyncConfig(keychain_service="my-vault")
    sync.add_secret(SecretEntry(name="custom", account="custom-svc", env_name="CUSTOM_KEY"))
    monkeypatch.setattr(cli.SyncConfig, "load", classmethod(lambda cls, path=None: sync))
    seen = []

    def exists(self, provider):
        seen.append((self.prefix, provider))
        return provider in {"openai", "custom-svc"}
    monkeypatch.setattr(keychain.KeychainStore, "exists", exists)
    _run(monkeypatch, "list", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["providers"] == ["custom-svc", "openai"]
    assert out["budget"]["budget_enabled"] is False
    assert {prefix for prefix, _ in seen} == {"my-vault"}  # same namespace as `banto store`


def test_profile_works_without_budget_and_persists(home, monkeypatch, capsys):
    bundled_before = BUNDLED_CONFIG.read_text()
    _run(monkeypatch, "profile")
    assert "balanced" in capsys.readouterr().out
    _run(monkeypatch, "profile", "budget")
    assert "Active profile set to 'budget'" in capsys.readouterr().out
    assert json.loads((home / "config.json").read_text())["active_profile"] == "budget"
    assert BUNDLED_CONFIG.read_text() == bundled_before
    _run(monkeypatch, "profile", "--json")
    assert json.loads(capsys.readouterr().out)["budget"]["active"] is True
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "profile", "nope")
    assert exc.value.code == 1


def test_check_budget_disabled_is_allowed(home, monkeypatch, capsys):
    _run(monkeypatch, "check", "gpt-4o", "--tokens", "10", "10")
    out = capsys.readouterr().out
    assert "ALLOWED (budget disabled" in out and "BLOCKED" not in out
    _run(monkeypatch, "check", "gpt-4o", "--tokens", "10", "10", "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["allowed"] is True and data["budget_enabled"] is False


def test_check_applies_model_and_provider_limits(home, monkeypatch, capsys, tmp_path):
    _budget_config(home, model_limits={"gpt-4o": 0.00001}, provider_limits={"openai": 5})
    monkeypatch.setattr(guard_module.CostGuard, "_get_usage_file_path",
                        lambda self: tmp_path / "usage.json")
    _run(monkeypatch, "check", "gpt-4o", "--tokens", "1000", "1000")
    out = capsys.readouterr().out
    assert "BLOCKED (over model 'gpt-4o' limit" in out
    _run(monkeypatch, "check", "gpt-4o-mini", "--tokens", "10", "10", "--json")
    assert json.loads(capsys.readouterr().out)["allowed"] is True


def test_check_unknown_model_exits_1(home, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "check", "no-such-model", "--tokens", "1", "1")
    assert exc.value.code == 1
    assert "Unknown model" in capsys.readouterr().err


def test_set_budget_never_writes_into_package(home, monkeypatch):
    bundled_before = BUNDLED_CONFIG.read_text()
    g = guard_module.CostGuard(caller="test", data_dir=str(home / "data"))
    assert g.config_path.resolve() == BUNDLED_CONFIG.resolve()
    g.set_budget(global_limit=12)
    assert BUNDLED_CONFIG.read_text() == bundled_before
    assert json.loads((home / "config.json").read_text())["monthly_limit_usd"] == 12


def test_register_x_oauth_points_to_pkce_enrolment(home, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "register-x-oauth")
    assert exc.value.code == 2
    assert "banto sync x-oauth-authorize" in capsys.readouterr().err


def test_register_x_oauth_not_routed_or_dispatched_in_broker_mode(tmp_path, monkeypatch):
    from banto import broker, broker_cli
    from banto.broker_client import BrokerClient, BrokerError
    (tmp_path / "required").touch()
    monkeypatch.setattr(broker_cli, "runtime_dir", lambda: tmp_path)
    call = Mock()
    monkeypatch.setattr(BrokerClient, "call", call)
    assert broker_cli.route_if_enabled(["register-x-oauth"]) is False
    call.assert_not_called()
    with pytest.raises(BrokerError, match="operation_not_allowed"):
        broker.dispatch("register_x_oauth", {})


def test_help_lists_register_asc_and_broker_mode(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--help")
    out = capsys.readouterr().out
    assert "register-asc" in out and "Broker mode" in out

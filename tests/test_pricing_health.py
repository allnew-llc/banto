"""5.3.0 pricing staleness / unpriced-profile warnings."""
import json
import sys
from datetime import datetime

import pytest

import banto.__main__ as cli
from banto import guard as guard_module
from banto.guard import BUNDLED_CONFIG, pricing_health
from banto.profiles import DEFAULT_PROFILES

BUNDLED_PRICING = json.loads((BUNDLED_CONFIG.parent / "pricing.json").read_text())


def test_bundled_pricing_has_machine_readable_dates():
    datetime.strptime(BUNDLED_PRICING["_updated"], "%Y-%m-%d")
    for model, entry in BUNDLED_PRICING.items():
        if not model.startswith("_"):
            datetime.strptime(entry["verified"], "%Y-%m-%d")


def test_default_profile_models_are_priced():
    models = {m for prof in DEFAULT_PROFILES.values() for m in prof.values()}
    assert pricing_health(BUNDLED_PRICING, sorted(models))["unpriced_profile_models"] == []


def test_stale_and_unpriced_detection():
    pricing = {
        "_updated": "2026-01-01",
        "fresh": {"type": "per_token", "input_per_1k": 1, "output_per_1k": 1, "verified": "2026-10-01"},
        "inherits": {"type": "per_token", "input_per_1k": 1, "output_per_1k": 1},
        "garbage": {"type": "per_token", "input_per_1k": 1, "output_per_1k": 1, "verified": "soon"},
    }
    h = pricing_health(pricing, ["fresh", "missing"], today=datetime(2026, 10, 17))
    assert h["stale_models"] == ["garbage", "inherits"]
    assert h["oldest_verified"] == "2026-01-01"
    assert h["unpriced_profile_models"] == ["missing"]
    assert pricing_health(pricing, today=datetime(2026, 2, 1))["stale_models"] == ["garbage"]


def test_extra_verified_key_does_not_affect_estimates():
    g = guard_module.CostGuard.__new__(guard_module.CostGuard)
    g.pricing = BUNDLED_PRICING
    assert g.estimate_cost("claude-haiku-4-5", input_tokens=1000, output_tokens=1000) == pytest.approx(0.006)
    assert g.estimate_cost("text-embedding-3-small", input_tokens=1_000_000, output_tokens=0) == pytest.approx(0.02)


@pytest.fixture
def stale_home(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(guard_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli, "CONFIG_DIR", config_dir)
    pricing = dict(BUNDLED_PRICING)
    pricing["_updated"] = "2020-01-01"
    pricing["gpt-4o"] = {**pricing["gpt-4o"], "verified": "2020-01-01"}
    pricing.pop("claude-haiku-4-5")
    (config_dir / "pricing.json").write_text(json.dumps(pricing))
    (config_dir / "config.json").write_text(BUNDLED_CONFIG.read_text())
    return config_dir


def test_status_warns_budget_disabled(stale_home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["banto", "status"])
    cli.main()
    out = capsys.readouterr().out
    assert "last verified more than 90 days ago (oldest: 2020-01-01)" in out
    assert "claude-haiku-4-5" in out and "unpriced_model" in out
    monkeypatch.setattr(sys, "argv", ["banto", "status", "--json"])
    cli.main()
    data = json.loads(capsys.readouterr().out)
    assert data["budget_enabled"] is False
    assert "gpt-4o" in data["pricing"]["stale_models"]


def test_status_warns_budget_enabled(stale_home, monkeypatch, capsys, tmp_path):
    config = json.loads(BUNDLED_CONFIG.read_text())
    config["monthly_limit_usd"] = 10
    (stale_home / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(guard_module.CostGuard, "_get_usage_file_path",
                        lambda self: tmp_path / "usage.json")
    monkeypatch.setattr(sys, "argv", ["banto", "status"])
    cli.main()
    out = capsys.readouterr().out
    assert "Remaining:" in out and "last verified more than 90 days" in out
    monkeypatch.setattr(sys, "argv", ["banto", "status", "--json"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["pricing"]["unpriced_profile_models"] == ["claude-haiku-4-5"]

"""Tests for CostGuard.recommend_profile()."""

import json
import tempfile
from pathlib import Path

import pytest

from banto.guard import CostGuard


@pytest.fixture()
def tmp_env(tmp_path: Path):
    """Create a minimal config + pricing + data dir for CostGuard."""
    config = {
        "monthly_limit_usd": 100.0,
        "provider_limits": {},
        "model_limits": {},
        "providers": {},
    }
    pricing = {
        "test-model": {
            "type": "per_token",
            "input_per_1k": 0.001,
            "output_per_1k": 0.002,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(json.dumps(pricing), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return config_path, data_dir


def _make_guard(config_path: Path, data_dir: Path) -> CostGuard:
    return CostGuard(
        config_path=str(config_path),
        caller="test",
        data_dir=str(data_dir),
    )


def _record_cost(guard: CostGuard, cost_usd: float) -> None:
    """Record usage that costs approximately cost_usd.

    Uses test-model with per_token pricing (input=0.001/1k, output=0.002/1k).
    To get a target cost: cost = (input/1000)*0.001 + (output/1000)*0.002.
    Setting input_tokens=0 and output_tokens = cost / 0.002 * 1000.
    """
    output_tokens = int(cost_usd / 0.002 * 1000)
    guard.record_usage(
        model="test-model",
        input_tokens=0,
        output_tokens=output_tokens,
        provider="test",
        operation="test",
    )


class TestRecommendProfile:
    """Tests for recommend_profile() threshold logic."""

    def test_quality_when_fresh_budget(self, tmp_env):
        """No usage at all -> >50% remaining -> 'quality'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        assert guard.recommend_profile() == "quality"

    def test_quality_when_low_usage(self, tmp_env):
        """30% used (70% remaining) -> >50% -> 'quality'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 30.0)  # 30/100 = 30% used, 70% remaining
        assert guard.recommend_profile() == "quality"

    def test_balanced_when_moderate_usage(self, tmp_env):
        """53% used (47% remaining) -> >20% and <=50% -> 'balanced'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 53.0)  # 53/100 = 53% used, 47% remaining
        assert guard.recommend_profile() == "balanced"

    def test_budget_when_high_usage(self, tmp_env):
        """85% used (15% remaining) -> <=20% -> 'budget'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 85.0)  # 85/100 = 85% used, 15% remaining
        assert guard.recommend_profile() == "budget"

    def test_budget_when_fully_used(self, tmp_env):
        """100% used (0% remaining) -> <=20% -> 'budget'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 100.0)
        assert guard.recommend_profile() == "budget"

    def test_quality_when_no_limit(self, tmp_env):
        """monthly_limit_usd=0 means unlimited -> 'quality'."""
        config_path, data_dir = tmp_env
        # Rewrite config with limit=0
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["monthly_limit_usd"] = 0
        config_path.write_text(json.dumps(config), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)
        assert guard.recommend_profile() == "quality"

    def test_quality_when_negative_limit(self, tmp_env):
        """Negative limit treated as no limit -> 'quality'."""
        config_path, data_dir = tmp_env
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["monthly_limit_usd"] = -5
        config_path.write_text(json.dumps(config), encoding="utf-8")

        guard = _make_guard(config_path, data_dir)
        assert guard.recommend_profile() == "quality"

    def test_edge_exactly_50_percent(self, tmp_env):
        """Exactly 50% remaining -> NOT >50%, so 'balanced'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 50.0)  # 50/100 = exactly 50% remaining
        assert guard.recommend_profile() == "balanced"

    def test_edge_exactly_20_percent(self, tmp_env):
        """Exactly 20% remaining -> NOT >20%, so 'budget'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 80.0)  # 80/100 = exactly 20% remaining
        assert guard.recommend_profile() == "budget"

    def test_edge_just_above_50_percent(self, tmp_env):
        """49% used (51% remaining) -> >50% -> 'quality'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 49.0)
        assert guard.recommend_profile() == "quality"

    def test_edge_just_above_20_percent(self, tmp_env):
        """79% used (21% remaining) -> >20% -> 'balanced'."""
        config_path, data_dir = tmp_env
        guard = _make_guard(config_path, data_dir)
        _record_cost(guard, 79.0)
        assert guard.recommend_profile() == "balanced"

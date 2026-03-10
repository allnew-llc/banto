"""Tests for model profile configuration (MPC-01 through MPC-04)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from banto.profiles import ProfileManager, DEFAULT_PROFILES
from banto.vault import SecureVault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_env(tmp_path: Path):
    """Create a minimal config + pricing + data dir for SecureVault."""
    config = {
        "monthly_limit_usd": 100.0,
        "hold_timeout_hours": 24,
        "provider_limits": {},
        "model_limits": {},
        "model_profiles": {
            "quality": {
                "chat": "claude-opus-4-6",
                "verify": "claude-sonnet-4-6",
                "embed": "text-embedding-3-large",
            },
            "balanced": {
                "chat": "claude-sonnet-4-6",
                "verify": "claude-haiku-4-5",
                "embed": "text-embedding-3-small",
            },
            "budget": {
                "chat": "claude-haiku-4-5",
                "verify": "claude-haiku-4-5",
                "embed": "text-embedding-3-small",
            },
        },
        "active_profile": "balanced",
        "providers": {
            "anthropic": {
                "models": [
                    "claude-opus-4-6",
                    "claude-sonnet-4-6",
                    "claude-haiku-4-5",
                ]
            },
            "openai": {
                "models": [
                    "gpt-4o",
                    "text-embedding-3-large",
                    "text-embedding-3-small",
                ]
            },
        },
    }
    pricing = {
        "claude-opus-4-6": {
            "type": "per_token",
            "input_per_1k": 0.015,
            "output_per_1k": 0.075,
        },
        "claude-sonnet-4-6": {
            "type": "per_token",
            "input_per_1k": 0.003,
            "output_per_1k": 0.015,
        },
        "claude-haiku-4-5": {
            "type": "per_token",
            "input_per_1k": 0.0008,
            "output_per_1k": 0.004,
        },
        "text-embedding-3-large": {
            "type": "per_token",
            "input_per_1k": 0.00013,
            "output_per_1k": 0.0,
        },
        "text-embedding-3-small": {
            "type": "per_token",
            "input_per_1k": 0.00002,
            "output_per_1k": 0.0,
        },
        "gpt-4o": {
            "type": "per_token",
            "input_per_1k": 0.005,
            "output_per_1k": 0.015,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(json.dumps(pricing), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return config_path, data_dir, config


def _make_vault(config_path: Path, data_dir: Path) -> SecureVault:
    """Create a SecureVault with a mock backend (no real Keychain)."""
    mock_backend = MagicMock()
    mock_backend.get.return_value = "sk-test-key-123"
    mock_backend.exists.return_value = True
    mock_backend.list_providers.return_value = ["anthropic", "openai"]

    return SecureVault(
        caller="test",
        backend=mock_backend,
        config_path=str(config_path),
        data_dir=str(data_dir),
    )


# ---------------------------------------------------------------------------
# ProfileManager unit tests
# ---------------------------------------------------------------------------

class TestProfileManager:
    """Unit tests for ProfileManager."""

    def test_default_active_profile(self):
        """Default active profile is 'balanced'."""
        pm = ProfileManager({})
        assert pm.active_profile == "balanced"

    def test_default_profiles_loaded(self):
        """When config has no profiles, defaults are used."""
        pm = ProfileManager({})
        profiles = pm.list_profiles()
        assert "quality" in profiles
        assert "balanced" in profiles
        assert "budget" in profiles

    def test_custom_profiles_from_config(self):
        """Custom profiles from config override defaults."""
        config = {
            "model_profiles": {
                "fast": {"chat": "gpt-4o-mini", "embed": "text-embedding-3-small"},
                "smart": {"chat": "gpt-4o", "embed": "text-embedding-3-large"},
            },
            "active_profile": "fast",
        }
        pm = ProfileManager(config)
        assert pm.active_profile == "fast"
        profiles = pm.list_profiles()
        assert "fast" in profiles
        assert "smart" in profiles
        assert "quality" not in profiles  # defaults replaced

    def test_resolve_model_chat(self):
        """Resolving 'chat' role returns the correct model."""
        pm = ProfileManager({})
        model = pm.resolve_model("chat")
        assert model == DEFAULT_PROFILES["balanced"]["chat"]

    def test_resolve_model_verify(self):
        """Resolving 'verify' role returns the correct model."""
        pm = ProfileManager({})
        model = pm.resolve_model("verify")
        assert model == DEFAULT_PROFILES["balanced"]["verify"]

    def test_resolve_model_embed(self):
        """Resolving 'embed' role returns the correct model."""
        pm = ProfileManager({})
        model = pm.resolve_model("embed")
        assert model == DEFAULT_PROFILES["balanced"]["embed"]

    def test_resolve_after_profile_switch(self):
        """Switching profile changes resolved models."""
        pm = ProfileManager({})
        pm.active_profile = "quality"
        assert pm.resolve_model("chat") == DEFAULT_PROFILES["quality"]["chat"]

    def test_invalid_role_raises_valueerror(self):
        """Unknown role raises ValueError."""
        pm = ProfileManager({})
        with pytest.raises(ValueError, match="Unknown role 'summarize'"):
            pm.resolve_model("summarize")

    def test_invalid_profile_raises_valueerror(self):
        """Setting an unknown profile raises ValueError."""
        pm = ProfileManager({})
        with pytest.raises(ValueError, match="Unknown profile: ultra"):
            pm.active_profile = "ultra"

    def test_list_profiles_active_indicator(self):
        """list_profiles marks the active profile correctly."""
        pm = ProfileManager({})
        profiles = pm.list_profiles()
        assert profiles["balanced"]["active"] is True
        assert profiles["quality"]["active"] is False
        assert profiles["budget"]["active"] is False

    def test_active_profile_from_config(self):
        """Active profile from config is respected."""
        config = {"active_profile": "quality"}
        pm = ProfileManager(config)
        assert pm.active_profile == "quality"

    def test_invalid_active_profile_in_config_falls_back(self):
        """If config has an invalid active_profile, fall back to default."""
        config = {"active_profile": "nonexistent"}
        pm = ProfileManager(config)
        assert pm.active_profile == "balanced"


# ---------------------------------------------------------------------------
# SecureVault integration tests (role= parameter)
# ---------------------------------------------------------------------------

class TestVaultGetKeyRole:
    """Tests for get_key() with role= parameter."""

    def test_role_resolves_to_model(self, tmp_env):
        """get_key(role='chat') resolves through active profile."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        # balanced profile: chat -> claude-sonnet-4-6
        key = vault.get_key(
            role="chat",
            input_tokens=100,
            output_tokens=50,
        )
        assert key == "sk-test-key-123"

    def test_model_takes_priority_over_role(self, tmp_env):
        """When both model= and role= are provided, model= wins."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        # Explicitly provide model= to override role=
        key = vault.get_key(
            model="gpt-4o",
            role="chat",  # would resolve to claude-sonnet-4-6, but model= wins
            input_tokens=100,
            output_tokens=50,
        )
        assert key == "sk-test-key-123"

    def test_neither_model_nor_role_raises_valueerror(self, tmp_env):
        """get_key() with neither model nor role raises ValueError."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        with pytest.raises(ValueError, match="Either 'model' or 'role'"):
            vault.get_key(input_tokens=100, output_tokens=50)

    def test_backward_compat_model_only(self, tmp_env):
        """get_key(model='gpt-4o') still works unchanged."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        key = vault.get_key(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
        )
        assert key == "sk-test-key-123"

    def test_role_with_profile_switch(self, tmp_env):
        """After switching profile, role resolves to different model."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        vault.set_profile("quality")

        # quality profile: chat -> claude-opus-4-6
        key = vault.get_key(
            role="chat",
            input_tokens=100,
            output_tokens=50,
        )
        assert key == "sk-test-key-123"

    def test_invalid_role_in_get_key(self, tmp_env):
        """get_key(role='invalid') raises ValueError."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        with pytest.raises(ValueError, match="Unknown role"):
            vault.get_key(role="invalid", input_tokens=100, output_tokens=50)


# ---------------------------------------------------------------------------
# SecureVault profile management tests
# ---------------------------------------------------------------------------

class TestVaultProfileManagement:
    """Tests for set_profile() and get_profiles()."""

    def test_set_profile(self, tmp_env):
        """set_profile() switches the active profile."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        vault.set_profile("budget")
        profiles = vault.get_profiles()
        assert profiles["budget"]["active"] is True
        assert profiles["balanced"]["active"] is False

    def test_set_invalid_profile(self, tmp_env):
        """set_profile() with unknown name raises ValueError."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        with pytest.raises(ValueError, match="Unknown profile"):
            vault.set_profile("premium")

    def test_get_profiles_returns_all(self, tmp_env):
        """get_profiles() returns all configured profiles."""
        config_path, data_dir, _ = tmp_env
        vault = _make_vault(config_path, data_dir)

        profiles = vault.get_profiles()
        assert set(profiles.keys()) == {"quality", "balanced", "budget"}
        for name, info in profiles.items():
            assert "models" in info
            assert "active" in info

    def test_default_profiles_when_no_config(self, tmp_path):
        """Profiles use defaults when config has no model_profiles."""
        config = {
            "monthly_limit_usd": 100.0,
            "provider_limits": {},
            "model_limits": {},
            "providers": {"anthropic": {"models": ["claude-sonnet-4-6"]}},
        }
        pricing = {
            "claude-sonnet-4-6": {
                "type": "per_token",
                "input_per_1k": 0.003,
                "output_per_1k": 0.015,
            },
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        pricing_path = tmp_path / "pricing.json"
        pricing_path.write_text(json.dumps(pricing), encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "sk-test"
        mock_backend.exists.return_value = True
        mock_backend.list_providers.return_value = []

        vault = SecureVault(
            caller="test",
            backend=mock_backend,
            config_path=str(config_path),
            data_dir=str(data_dir),
        )
        profiles = vault.get_profiles()
        assert "quality" in profiles
        assert "balanced" in profiles
        assert "budget" in profiles
        assert profiles["balanced"]["active"] is True


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLIProfile:
    """Tests for banto profile CLI command."""

    def test_profile_show(self, tmp_env, capsys, monkeypatch):
        """'banto profile' shows all profiles."""
        config_path, data_dir, _ = tmp_env
        monkeypatch.setattr(
            "sys.argv", ["banto", "profile"]
        )

        # Patch SecureVault to use our test config
        from banto.__main__ import cmd_profile
        from unittest.mock import patch

        mock_vault = MagicMock()
        mock_vault.get_profiles.return_value = {
            "quality": {
                "models": {"chat": "claude-opus-4-6"},
                "active": False,
            },
            "balanced": {
                "models": {"chat": "claude-sonnet-4-6"},
                "active": True,
            },
        }

        with patch("banto.__main__.SecureVault", return_value=mock_vault):
            cmd_profile([])

        captured = capsys.readouterr()
        assert "balanced" in captured.out
        assert "quality" in captured.out

    def test_profile_set(self, tmp_env, capsys, monkeypatch):
        """'banto profile budget' sets the active profile."""
        config_path, data_dir, _ = tmp_env

        from banto.__main__ import cmd_profile
        from unittest.mock import patch

        mock_vault = MagicMock()

        with patch("banto.__main__.SecureVault", return_value=mock_vault):
            cmd_profile(["budget"])

        mock_vault.set_profile.assert_called_once_with("budget")
        captured = capsys.readouterr()
        assert "budget" in captured.out

    def test_profile_set_invalid(self, tmp_env, capsys, monkeypatch):
        """'banto profile invalid' exits with error."""
        config_path, data_dir, _ = tmp_env

        from banto.__main__ import cmd_profile
        from unittest.mock import patch

        mock_vault = MagicMock()
        mock_vault.set_profile.side_effect = ValueError(
            "Unknown profile: invalid. Available: ['quality', 'balanced', 'budget']"
        )

        with patch("banto.__main__.SecureVault", return_value=mock_vault):
            with pytest.raises(SystemExit) as exc_info:
                cmd_profile(["invalid"])
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Unknown profile" in captured.err

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.sync.config — sync.json read/write."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from banto.sync.config import Environment, SecretEntry, Target, SyncConfig


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    return tmp_path / "sync.json"


@pytest.fixture
def sample_config(tmp_config: Path) -> SyncConfig:
    config = SyncConfig(keychain_service="test-vault")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        description="OpenAI API Key",
        targets=[
            Target(platform="cloudflare-pages", project="my-project"),
            Target(platform="local", file="/tmp/test/.dev.vars"),
        ],
    ))
    config.add_secret(SecretEntry(
        name="gemini",
        account="gemini",
        env_name="GEMINI_API_KEY",
        description="Gemini API Key",
        targets=[
            Target(platform="vercel", project="my-app"),
        ],
    ))
    return config


class TestTarget:
    def test_from_dict_cloudflare(self):
        t = Target.from_dict({"platform": "cloudflare-pages", "project": "myapp"})
        assert t.platform == "cloudflare-pages"
        assert t.project == "myapp"
        assert t.label == "cloudflare-pages:myapp"

    def test_from_dict_local(self):
        t = Target.from_dict({"platform": "local", "file": "/tmp/.dev.vars"})
        assert t.platform == "local"
        assert t.file == "/tmp/.dev.vars"
        assert t.label == "/tmp/.dev.vars"

    def test_to_dict_cloudflare(self):
        t = Target(platform="cloudflare-pages", project="myapp")
        d = t.to_dict()
        assert d == {"platform": "cloudflare-pages", "project": "myapp"}

    def test_to_dict_local(self):
        t = Target(platform="local", file="/tmp/.dev.vars")
        d = t.to_dict()
        assert d == {"platform": "local", "file": "/tmp/.dev.vars"}


class TestSecretEntry:
    def test_from_dict(self):
        data = {
            "account": "my-acct",
            "env_name": "MY_KEY",
            "description": "desc",
            "targets": [
                {"platform": "vercel", "project": "app1"},
            ],
        }
        entry = SecretEntry.from_dict("test", data)
        assert entry.name == "test"
        assert entry.account == "my-acct"
        assert entry.env_name == "MY_KEY"
        assert len(entry.targets) == 1
        assert entry.targets[0].platform == "vercel"

    def test_roundtrip(self):
        entry = SecretEntry(
            name="x", account="x-acct", env_name="X_KEY",
            description="test",
            targets=[Target(platform="local", file="/tmp/x")],
        )
        d = entry.to_dict()
        restored = SecretEntry.from_dict("x", d)
        assert restored.account == entry.account
        assert restored.env_name == entry.env_name
        assert len(restored.targets) == 1


class TestSyncConfig:
    def test_load_nonexistent(self, tmp_path: Path):
        config = SyncConfig.load(tmp_path / "missing.json")
        assert config.version == 1
        assert len(config.secrets) == 0

    def test_save_and_load(self, tmp_config: Path, sample_config: SyncConfig):
        sample_config.save(tmp_config)
        assert tmp_config.exists()

        loaded = SyncConfig.load(tmp_config)
        assert loaded.keychain_service == "test-vault"
        assert len(loaded.secrets) == 2
        assert "openai" in loaded.secrets
        assert "gemini" in loaded.secrets

        openai = loaded.secrets["openai"]
        assert openai.env_name == "OPENAI_API_KEY"
        assert len(openai.targets) == 2
        assert openai.targets[0].platform == "cloudflare-pages"

    def test_add_and_remove(self):
        config = SyncConfig()
        entry = SecretEntry(name="test", account="t", env_name="TEST_KEY")
        config.add_secret(entry)
        assert "test" in config.secrets

        removed = config.remove_secret("test")
        assert removed is not None
        assert removed.name == "test"
        assert "test" not in config.secrets

    def test_remove_nonexistent(self):
        config = SyncConfig()
        assert config.remove_secret("nope") is None

    def test_get_secret(self):
        config = SyncConfig()
        config.add_secret(SecretEntry(name="a", account="a", env_name="A"))
        assert config.get_secret("a") is not None
        assert config.get_secret("b") is None

    def test_load_invalid_json(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("just a string\n")
        config = SyncConfig.load(bad)
        assert len(config.secrets) == 0


class TestEnvironmentInheritance:
    def test_resolve_base_only(self):
        config = SyncConfig()
        config.add_secret(SecretEntry(name="key1", account="k1", env_name="KEY1"))
        resolved = config.resolve_environment("nonexistent")
        assert "key1" in resolved

    def test_resolve_inherits_base(self):
        """Environment inherits all base secrets"""
        config = SyncConfig()
        config.add_secret(SecretEntry(name="shared", account="s", env_name="SHARED"))
        config.environments["production"] = Environment(
            name="production", inherits="",
        )
        resolved = config.resolve_environment("production")
        assert "shared" in resolved

    def test_override(self):
        """Environment overrides a base secret"""
        config = SyncConfig()
        config.add_secret(SecretEntry(
            name="db", account="db", env_name="DB_HOST",
            targets=[Target(platform="local", file="/tmp/base.env")],
        ))
        config.environments["production"] = Environment(
            name="production",
            secrets={
                "db": SecretEntry(
                    name="db", account="db-prod", env_name="DB_HOST",
                    targets=[Target(platform="local", file="/tmp/prod.env")],
                ),
            },
        )
        resolved = config.resolve_environment("production")
        assert resolved["db"].account == "db-prod"
        assert resolved["db"].targets[0].file == "/tmp/prod.env"

    def test_env_adds_secret(self):
        """Environment adds a secret not in base"""
        config = SyncConfig()
        config.add_secret(SecretEntry(name="shared", account="s", env_name="SHARED"))
        config.environments["staging"] = Environment(
            name="staging",
            secrets={
                "stg-only": SecretEntry(name="stg-only", account="stg", env_name="STG_KEY"),
            },
        )
        resolved = config.resolve_environment("staging")
        assert "shared" in resolved
        assert "stg-only" in resolved

    def test_chain_inheritance(self):
        """dev inherits base, dev_personal inherits dev"""
        config = SyncConfig()
        config.add_secret(SecretEntry(name="base-key", account="b", env_name="BASE"))
        config.environments["dev"] = Environment(
            name="dev", inherits="",
            secrets={
                "dev-key": SecretEntry(name="dev-key", account="d", env_name="DEV"),
            },
        )
        config.environments["dev_personal"] = Environment(
            name="dev_personal", inherits="dev",
            secrets={
                "personal": SecretEntry(name="personal", account="p", env_name="PERSONAL"),
            },
        )
        resolved = config.resolve_environment("dev_personal")
        assert "base-key" in resolved
        assert "dev-key" in resolved
        assert "personal" in resolved

    def test_circular_inheritance_safety(self):
        """Circular inheritance doesn't loop forever"""
        config = SyncConfig()
        config.environments["a"] = Environment(name="a", inherits="b")
        config.environments["b"] = Environment(name="b", inherits="a")
        # Should not hang
        resolved = config.resolve_environment("a")
        assert isinstance(resolved, dict)

    def test_save_load_with_environments(self, tmp_path: Path):
        cfg_path = tmp_path / "sync.json"
        config = SyncConfig()
        config.add_secret(SecretEntry(name="x", account="x", env_name="X"))
        config.environments["production"] = Environment(
            name="production",
            secrets={"y": SecretEntry(name="y", account="y", env_name="Y")},
        )
        config.default_environment = "production"
        config.save(cfg_path)

        loaded = SyncConfig.load(cfg_path)
        assert "production" in loaded.environments
        assert "y" in loaded.environments["production"].secrets
        assert loaded.default_environment == "production"

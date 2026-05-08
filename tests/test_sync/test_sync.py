# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.sync.sync — sync orchestration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from banto.sync.config import SecretEntry, Target, SyncConfig
from banto.sync.sync import _sync_one_target, check_status, remove_secret, sync_all, sync_secret


@pytest.fixture
def config() -> SyncConfig:
    cfg = SyncConfig(keychain_service="test-vault")
    cfg.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        targets=[
            Target(platform="cloudflare-pages", project="proj1"),
            Target(platform="local", file="/tmp/test/.dev.vars"),
        ],
    ))
    cfg.add_secret(SecretEntry(
        name="gemini",
        account="gemini",
        env_name="GEMINI_API_KEY",
        targets=[
            Target(platform="vercel", project="app1"),
        ],
    ))
    return cfg


class TestSyncSecret:
    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_sync_success(self, mock_kc_cls, mock_get_driver, config, tmp_path):
        mock_kc = MagicMock()
        mock_kc.get.return_value = "secret-val"
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.put.return_value = True
        mock_get_driver.return_value = mock_driver

        audit_log = tmp_path / "audit.log"
        report = sync_secret(config, "openai", audit_log=audit_log)

        assert report.all_ok
        assert report.ok_count == 2
        assert mock_driver.put.call_count == 2
        # Verify audit log was written
        assert audit_log.exists()

    @patch("banto.sync.sync.KeychainStore")
    def test_sync_keychain_missing(self, mock_kc_cls, config, tmp_path):
        mock_kc = MagicMock()
        mock_kc.get.return_value = None
        mock_kc_cls.return_value = mock_kc

        report = sync_secret(config, "openai", audit_log=tmp_path / "audit.log")
        assert not report.all_ok
        assert report.fail_count == 1

    def test_sync_unknown_secret(self, config, tmp_path):
        report = sync_secret(config, "nonexistent", audit_log=tmp_path / "audit.log")
        assert not report.all_ok
        assert "not found" in report.results[0].message

    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_sync_driver_failure(self, mock_kc_cls, mock_get_driver, config, tmp_path):
        mock_kc = MagicMock()
        mock_kc.get.return_value = "secret-val"
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.put.return_value = False
        mock_get_driver.return_value = mock_driver

        report = sync_secret(config, "openai", audit_log=tmp_path / "audit.log")
        assert not report.all_ok
        assert report.fail_count == 2

    def test_sync_one_target_passes_environments_to_supported_driver(self, tmp_path):
        class EnvAwareDriver:
            def __init__(self):
                self.environments = None

            def put(self, env_name, value, project, environments=None):
                self.environments = environments
                return True

        driver = EnvAwareDriver()
        entry = SecretEntry(name="xai", account="xai", env_name="XAI_API_KEY")
        target = Target(
            platform="vercel",
            project="voice-gateway",
            environments=["production", "preview"],
        )

        with patch("banto.sync.sync.get_driver", return_value=driver):
            result = _sync_one_target(
                entry,
                target,
                "secret-value",
                audit_log=tmp_path / "audit.log",
            )

        assert result.success is True
        assert driver.environments == ["production", "preview"]

    def test_sync_one_target_ignores_environments_for_legacy_driver(self, tmp_path):
        class LegacyDriver:
            def __init__(self):
                self.called = False

            def put(self, env_name, value, project):
                self.called = True
                return True

        driver = LegacyDriver()
        entry = SecretEntry(name="local", account="local", env_name="LOCAL_SECRET")
        target = Target(
            platform="local",
            file=str(tmp_path / ".dev.vars"),
            environments=["production"],
        )

        with patch("banto.sync.sync.get_driver", return_value=driver):
            result = _sync_one_target(
                entry,
                target,
                "secret-value",
                audit_log=tmp_path / "audit.log",
            )

        assert result.success is True
        assert driver.called is True


class TestSyncAll:
    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_sync_all(self, mock_kc_cls, mock_get_driver, config, tmp_path):
        mock_kc = MagicMock()
        mock_kc.get.return_value = "val"
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.put.return_value = True
        mock_get_driver.return_value = mock_driver

        report = sync_all(config, audit_log=tmp_path / "audit.log")
        assert report.all_ok
        assert report.ok_count == 3  # 2 openai targets + 1 gemini target


class TestCheckStatus:
    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_check_status(self, mock_kc_cls, mock_get_driver, config):
        mock_kc = MagicMock()
        mock_kc.exists.return_value = True
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.exists.return_value = True
        mock_get_driver.return_value = mock_driver

        entries = check_status(config)
        assert len(entries) == 2
        assert entries[0].keychain_exists is True
        assert all(v is True for v in entries[0].target_status.values())

    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_check_status_missing(self, mock_kc_cls, mock_get_driver, config):
        mock_kc = MagicMock()
        mock_kc.exists.return_value = True
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.exists.return_value = False
        mock_get_driver.return_value = mock_driver

        entries = check_status(config)
        assert entries[0].keychain_exists is True
        assert all(v is False for v in entries[0].target_status.values())


class TestRemoveSecret:
    @patch("banto.sync.sync.get_driver")
    @patch("banto.sync.sync.KeychainStore")
    def test_remove_success(self, mock_kc_cls, mock_get_driver, config, tmp_path):
        mock_kc = MagicMock()
        mock_kc.delete.return_value = True
        mock_kc_cls.return_value = mock_kc
        mock_driver = MagicMock()
        mock_driver.delete.return_value = True
        mock_get_driver.return_value = mock_driver

        report = remove_secret(config, "openai", audit_log=tmp_path / "audit.log")
        assert report.ok_count == 2  # 2 targets
        assert "openai" not in config.secrets

    def test_remove_unknown(self, config, tmp_path):
        report = remove_secret(config, "nope", audit_log=tmp_path / "audit.log")
        assert not report.all_ok

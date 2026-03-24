# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto sync CLI commands — rotate, run, import, audit --max-age-days."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from banto.sync.cli import (
    cmd_sync_audit,
    cmd_sync_import,
    cmd_sync_rotate,
    cmd_sync_run,
)
from banto.sync.config import SecretEntry, SyncConfig, Target


@pytest.fixture
def sync_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    """Create a test sync config with one secret."""
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        description="OpenAI",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


class TestRotate:
    @patch("banto.sync.cli.HistoryStore")
    @patch("banto.sync.cli.KeychainStore")
    @patch("banto.sync.cli.sync_secret")
    @patch("banto.sync.cli.getpass.getpass", return_value="new-secret-value")
    def test_rotate_interactive(self, mock_getpass, mock_sync, mock_kc_cls, mock_hist_cls,
                                 sync_config, capsys):
        config, config_path = sync_config
        from banto.sync.sync import SyncReport
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_hist = mock_hist_cls.return_value
        mock_ver = MagicMock()
        mock_ver.version = 2
        mock_hist.record.return_value = mock_ver
        mock_sync.return_value = SyncReport()

        cmd_sync_rotate(["openai", "--config", str(config_path)])
        out = capsys.readouterr().out
        assert "Rotated" in out
        assert "v2" in out
        mock_kc.store.assert_called_once_with("openai", "new-secret-value")

    @patch("banto.sync.cli.sync_secret")
    @patch("banto.sync.cli.HistoryStore")
    @patch("banto.sync.cli.KeychainStore")
    @patch("banto.sync.cli.subprocess.run")
    def test_rotate_from_cli(self, mock_run, mock_kc_cls, mock_hist_cls, mock_sync,
                              sync_config, capsys):
        import subprocess as sp
        from banto.sync.sync import SyncReport
        config, config_path = sync_config
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cli-generated-key\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_ver = MagicMock()
        mock_ver.version = 3
        mock_hist_cls.return_value.record.return_value = mock_ver
        mock_sync.return_value = SyncReport()

        cmd_sync_rotate(["openai", "--from-cli", "echo test", "--config", str(config_path)])
        mock_kc.store.assert_called_once_with("openai", "cli-generated-key")

    def test_rotate_missing_name(self):
        with pytest.raises(SystemExit):
            cmd_sync_rotate([])

    @patch("banto.sync.cli.KeychainStore")
    def test_rotate_unknown_secret(self, mock_kc_cls, sync_config):
        _, config_path = sync_config
        with pytest.raises(SystemExit):
            cmd_sync_rotate(["nonexistent", "--config", str(config_path)])


class TestRun:
    @patch("banto.sync.cli.KeychainStore")
    @patch("banto.sync.cli.subprocess.run")
    def test_run_injects_env(self, mock_run, mock_kc_cls, sync_config):
        _, config_path = sync_config
        mock_kc = mock_kc_cls.return_value
        mock_kc.get.return_value = "sk-secret-123"
        mock_run.return_value = MagicMock(returncode=0)

        with pytest.raises(SystemExit) as exc:
            cmd_sync_run(["--config", str(config_path), "--", "echo", "hello"])
        assert exc.value.code == 0

        # Verify env was passed with secret
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env["OPENAI_API_KEY"] == "sk-secret-123"

    def test_run_no_command(self, sync_config):
        _, config_path = sync_config
        with pytest.raises(SystemExit):
            cmd_sync_run(["--config", str(config_path)])

    def test_run_no_double_dash(self, sync_config):
        _, config_path = sync_config
        with pytest.raises(SystemExit):
            cmd_sync_run(["--config", str(config_path), "echo", "hello"])


class TestImport:
    @patch("banto.sync.cli.HistoryStore")
    @patch("banto.sync.cli.KeychainStore")
    def test_import_env_file(self, mock_kc_cls, mock_hist_cls, tmp_path):
        config_path = tmp_path / "sync.json"
        SyncConfig(keychain_service="test-sync").save(config_path)

        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-test\nGEMINI_API_KEY=gem-test\n")

        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_hist_cls.return_value.record.return_value = MagicMock()

        cmd_sync_import([str(env_file), "--config", str(config_path)])

        assert mock_kc.store.call_count == 2
        # Verify config was saved with new entries
        loaded = SyncConfig.load(config_path)
        assert "openai-api-key" in loaded.secrets
        assert "gemini-api-key" in loaded.secrets

    @patch("banto.sync.cli.HistoryStore")
    @patch("banto.sync.cli.KeychainStore")
    def test_import_json_file(self, mock_kc_cls, mock_hist_cls, tmp_path):
        config_path = tmp_path / "sync.json"
        SyncConfig(keychain_service="test-sync").save(config_path)

        json_file = tmp_path / "secrets.json"
        json_file.write_text(json.dumps({"MY_TOKEN": "tok-123"}))

        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_hist_cls.return_value.record.return_value = MagicMock()

        cmd_sync_import([str(json_file), "--config", str(config_path)])

        loaded = SyncConfig.load(config_path)
        assert "my-token" in loaded.secrets
        assert loaded.secrets["my-token"].env_name == "MY_TOKEN"

    @patch("banto.sync.cli.HistoryStore")
    @patch("banto.sync.cli.KeychainStore")
    def test_import_skips_duplicates(self, mock_kc_cls, mock_hist_cls, tmp_path, capsys):
        config = SyncConfig(keychain_service="test-sync")
        config.add_secret(SecretEntry(name="my-key", account="my-key", env_name="MY_KEY"))
        config_path = tmp_path / "sync.json"
        config.save(config_path)

        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=value\n")

        mock_kc_cls.return_value.store.return_value = True
        mock_hist_cls.return_value.record.return_value = MagicMock()

        cmd_sync_import([str(env_file), "--config", str(config_path)])
        out = capsys.readouterr().out
        assert "Skip" in out
        assert "Imported 0" in out

    def test_import_file_not_found(self):
        with pytest.raises(SystemExit):
            cmd_sync_import(["/nonexistent/file"])


class TestAuditMaxAge:
    @patch("banto.sync.cli.check_status")
    @patch("banto.sync.cli.HistoryStore")
    def test_stale_secret_detected(self, mock_hist_cls, mock_check, sync_config):
        _, config_path = sync_config
        mock_check.return_value = []  # No drift issues

        # History shows last rotation 100 days ago
        from banto.sync.history import SecretVersion
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        mock_hist = mock_hist_cls.return_value
        mock_hist.list_versions.return_value = [
            SecretVersion(version=1, timestamp=old_ts, fingerprint="abc123def456"),
        ]

        with pytest.raises(SystemExit) as exc:
            cmd_sync_audit(["--max-age-days", "90", "--config", str(config_path)])
        assert exc.value.code == 1

    @patch("banto.sync.cli.check_status")
    @patch("banto.sync.cli.HistoryStore")
    def test_fresh_secret_passes(self, mock_hist_cls, mock_check, sync_config, capsys):
        _, config_path = sync_config
        mock_check.return_value = []

        from banto.sync.history import SecretVersion
        recent_ts = datetime.now(timezone.utc).isoformat()
        mock_hist = mock_hist_cls.return_value
        mock_hist.list_versions.return_value = [
            SecretVersion(version=1, timestamp=recent_ts, fingerprint="abc123def456"),
        ]

        cmd_sync_audit(["--max-age-days", "90", "--config", str(config_path)])
        out = capsys.readouterr().out
        assert "All secrets in sync" in out
        assert "90 days" in out

    @patch("banto.sync.cli.check_status")
    @patch("banto.sync.cli.HistoryStore")
    def test_no_history_flagged(self, mock_hist_cls, mock_check, sync_config):
        _, config_path = sync_config
        mock_check.return_value = []
        mock_hist_cls.return_value.list_versions.return_value = []

        with pytest.raises(SystemExit) as exc:
            cmd_sync_audit(["--max-age-days", "90", "--config", str(config_path)])
        assert exc.value.code == 1

    @patch("banto.sync.cli.check_status")
    def test_audit_without_max_age(self, mock_check, sync_config, capsys):
        """Normal audit without --max-age-days still works."""
        _, config_path = sync_config
        mock_check.return_value = []
        cmd_sync_audit(["--config", str(config_path)])
        out = capsys.readouterr().out
        assert "All secrets in sync" in out

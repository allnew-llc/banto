# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Safety checks for sync push argument handling."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from banto.sync.cli import cmd_sync_push
from banto.sync.config import SecretEntry, SyncConfig
from banto.sync.sync import SyncReport


@pytest.fixture
def push_config(tmp_path: Path) -> Path:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="stripe",
        account="stripe",
        env_name="STRIPE_SECRET_KEY",
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config_path


@patch("banto.sync.cli.sync_secret")
@patch("banto.sync.cli.sync_all")
def test_sync_push_help_has_no_side_effects(mock_sync_all, mock_sync_secret, push_config, capsys):
    cmd_sync_push(["--help", "--config", str(push_config)])

    out = capsys.readouterr().out
    assert "Usage: banto sync push" in out
    mock_sync_all.assert_not_called()
    mock_sync_secret.assert_not_called()


@patch("banto.sync.cli.sync_secret")
@patch("banto.sync.cli.sync_all")
def test_sync_push_rejects_unknown_flags_before_side_effects(
    mock_sync_all,
    mock_sync_secret,
    push_config,
):
    with pytest.raises(SystemExit):
        cmd_sync_push(["--sensitive", "--config", str(push_config)])

    mock_sync_all.assert_not_called()
    mock_sync_secret.assert_not_called()


@patch("banto.sync.cli.sync_secret")
@patch("banto.sync.cli.sync_all")
def test_sync_push_does_not_treat_config_path_as_secret_name(
    mock_sync_all,
    mock_sync_secret,
    push_config,
):
    mock_sync_all.return_value = SyncReport()

    cmd_sync_push(["--config", str(push_config)])

    mock_sync_all.assert_called_once()
    mock_sync_secret.assert_not_called()

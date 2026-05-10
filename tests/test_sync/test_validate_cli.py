# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for sync validation CLI filtering."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from banto.sync.cli import cmd_sync_validate
from banto.sync.config import SecretEntry, SyncConfig
from banto.sync.validate import ValidationResult


@pytest.fixture
def validate_config(tmp_path: Path) -> Path:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="stripe-live-secret",
        account="stripe-live-secret",
        env_name="STRIPE_SECRET_KEY",
    ))
    config.add_secret(SecretEntry(
        name="anthropic",
        account="anthropic",
        env_name="ANTHROPIC_API_KEY",
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config_path


@patch("banto.sync.validate.validate_key")
@patch("banto.sync.cli.KeychainStore")
def test_cmd_sync_validate_filters_requested_names(mock_kc_cls, mock_validate, validate_config, capsys):
    mock_kc_cls.return_value.get.return_value = "secret-value"
    mock_validate.return_value = ValidationResult(
        provider="stripe",
        valid=True,
        status="unknown",
        message="No validator available for this provider",
    )

    cmd_sync_validate(["stripe-live-secret", "--config", str(validate_config)])

    out = capsys.readouterr().out
    assert "Testing 1 key(s)" in out
    assert "stripe-live-secret" in out
    assert "anthropic" not in out
    mock_kc_cls.return_value.get.assert_called_once_with("stripe-live-secret")


def test_cmd_sync_validate_rejects_missing_requested_name(validate_config):
    with pytest.raises(SystemExit):
        cmd_sync_validate(["missing", "--config", str(validate_config)])

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for phase-2 propagation flow."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.cli import cmd_sync_propagate
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.propagation import (
    build_propagation_plan,
    propagate_secret,
    run_smoke_command,
    run_smoke_preset_check,
    validate_propagation_plan,
)
from banto.sync.sync import SyncReport
from banto.sync.validate import ValidationResult


@pytest.fixture
def propagate_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="github",
        account="github",
        env_name="GITHUB_TOKEN",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config.add_secret(SecretEntry(
        name="line-owner",
        account="line-owner",
        env_name="LINE_OWNER_USER_ID",
    ))
    config.add_secret(SecretEntry(
        name="hmac",
        account="hmac",
        env_name="HMAC_SECRET",
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


def test_build_propagation_plan_for_propagate_only(propagate_config):
    config, _ = propagate_config
    plan = build_propagation_plan(config, "github")
    assert plan.rotation_class == "propagate_only"
    assert plan.provider == "github"
    assert plan.is_allowed is True


def test_validate_propagation_plan_rejects_inventory_and_manual(propagate_config):
    config, _ = propagate_config

    with pytest.raises(ValueError):
        validate_propagation_plan(build_propagation_plan(config, "line-owner"))

    with pytest.raises(ValueError):
        validate_propagation_plan(build_propagation_plan(config, "hmac"))


@patch("banto.sync.propagation.sync_secret")
@patch("banto.sync.propagation.HistoryStore")
@patch("banto.sync.propagation.KeychainStore")
def test_propagate_secret_success(mock_kc_cls, mock_hist_cls, mock_sync, propagate_config):
    config, _ = propagate_config
    mock_kc_cls.return_value.store.return_value = True
    mock_ver = MagicMock()
    mock_ver.version = 4
    mock_hist_cls.return_value.record.return_value = mock_ver
    mock_sync.return_value = SyncReport()

    result = propagate_secret(config, "github", "ghp_new")

    assert result.ok is True
    assert result.version == 4
    mock_kc_cls.return_value.store.assert_called_once_with("github", "ghp_new")


@patch("banto.sync.propagation.sync_secret")
@patch("banto.sync.propagation.HistoryStore")
@patch("banto.sync.propagation.KeychainStore")
def test_propagate_secret_allows_manual_cutover_when_explicit(
    mock_kc_cls,
    mock_hist_cls,
    mock_sync,
    propagate_config,
):
    config, _ = propagate_config
    mock_kc_cls.return_value.store.return_value = True
    mock_ver = MagicMock()
    mock_ver.version = 9
    mock_hist_cls.return_value.record.return_value = mock_ver
    mock_sync.return_value = SyncReport()

    result = propagate_secret(config, "hmac", "replacement", allow_manual_cutover=True)

    assert result.ok is True
    assert result.plan.rotation_class == "manual_cutover"
    assert result.version == 9
    mock_kc_cls.return_value.store.assert_called_once_with("hmac", "replacement")


@patch("banto.sync.propagation.sync_secret")
@patch("banto.sync.propagation.HistoryStore")
@patch("banto.sync.propagation.KeychainStore")
def test_propagate_secret_rejects_conflicting_smoke_options_before_side_effects(
    mock_kc_cls,
    mock_hist_cls,
    mock_sync,
    propagate_config,
):
    config, _ = propagate_config

    result = propagate_secret(
        config,
        "github",
        "ghp_new",
        smoke_command="echo ok",
        smoke_preset="env-present",
    )

    assert result.ok is False
    assert result.stored is False
    assert result.smoke_check is not None
    assert "either smoke_command or smoke_preset" in result.smoke_check.message
    mock_kc_cls.return_value.store.assert_not_called()
    mock_hist_cls.return_value.record.assert_not_called()
    mock_sync.assert_not_called()


@patch("banto.sync.propagation.validate_key")
@patch("banto.sync.propagation.sync_secret")
@patch("banto.sync.propagation.HistoryStore")
@patch("banto.sync.propagation.KeychainStore")
def test_propagate_secret_validation_fail_blocks_store(
    mock_kc_cls,
    mock_hist_cls,
    mock_sync,
    mock_validate,
    propagate_config,
):
    config, _ = propagate_config
    mock_validate.return_value = ValidationResult(
        provider="github",
        valid=False,
        status="fail",
        message="Invalid token",
    )

    result = propagate_secret(config, "github", "bad", do_validate=True)

    assert result.ok is False
    assert result.validation is not None
    mock_validate.assert_called_once_with("github", "bad")
    mock_kc_cls.return_value.store.assert_not_called()
    mock_hist_cls.return_value.record.assert_not_called()
    mock_sync.assert_not_called()


@patch("banto.sync.propagation.subprocess.run")
def test_run_smoke_command(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    result = run_smoke_command("echo ok", env_name="GITHUB_TOKEN", value="ghp_new")
    assert result.success is True


@patch("banto.sync.smoke_presets.validate_key")
def test_run_smoke_preset_provider_validate(mock_validate, propagate_config):
    config, _ = propagate_config
    plan = build_propagation_plan(config, "github")
    mock_validate.return_value = ValidationResult(
        provider="github",
        valid=True,
        status="pass",
        message="Token valid",
    )

    result = run_smoke_preset_check(plan, "provider-validate", value="ghp_new")

    assert result.success is True
    assert result.command == "preset:provider-validate"


def test_run_smoke_preset_env_present(propagate_config):
    config, _ = propagate_config
    plan = build_propagation_plan(config, "github")

    result = run_smoke_preset_check(plan, "env-present", value="ghp_new")

    assert result.success is True
    assert result.command == "preset:env-present"


@patch("banto.sync.smoke_presets._run_openai_runtime_smoke")
def test_run_smoke_preset_openai_runtime(mock_runtime, propagate_config):
    config, _ = propagate_config
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        targets=[Target(platform="local", file="/tmp/.env")],
    ))
    plan = build_propagation_plan(config, "openai")
    mock_runtime.return_value = (True, "runtime ok")

    result = run_smoke_preset_check(plan, "openai-runtime", value="sk-new")

    assert result.success is True
    assert result.command == "preset:openai-runtime"
    assert result.message == "runtime ok"


def test_run_smoke_preset_openai_runtime_rejects_non_openai(propagate_config):
    config, _ = propagate_config
    plan = build_propagation_plan(config, "github")

    result = run_smoke_preset_check(plan, "openai-runtime", value="ghp_new")

    assert result.success is False
    assert "only supports OpenAI" in result.message


@patch("banto.sync.cli.propagate_secret")
def test_cmd_sync_propagate_dry_run(mock_propagate, propagate_config, capsys):
    _, config_path = propagate_config
    cmd_sync_propagate(["github", "--dry-run", "--config", str(config_path)])
    out = capsys.readouterr().out
    assert "BANTO SYNC PROPAGATE" in out
    assert "GITHUB_TOKEN" in out
    mock_propagate.assert_not_called()


@patch("banto.sync.cli.propagate_secret")
@patch("banto.sync.cli._resolve_new_value", return_value="ghp_new")
def test_cmd_sync_propagate_success(mock_resolve, mock_propagate, propagate_config, capsys):
    _, config_path = propagate_config
    mock_plan = build_propagation_plan(SyncConfig(
        secrets={
            "github": SecretEntry(name="github", account="github", env_name="GITHUB_TOKEN"),
        }
    ), "github")
    mock_propagate.return_value = MagicMock(
        ok=True,
        plan=mock_plan,
        version=5,
        validation=None,
        sync_report=SyncReport(),
        smoke_check=None,
    )

    cmd_sync_propagate(["github", "--config", str(config_path)])
    out = capsys.readouterr().out
    assert "Propagated 'github'" in out


def test_cmd_sync_propagate_rejects_manual_cutover(propagate_config):
    _, config_path = propagate_config
    with pytest.raises(SystemExit):
        cmd_sync_propagate(["hmac", "--dry-run", "--config", str(config_path)])


def test_cmd_sync_propagate_rejects_smoke_and_preset_together(propagate_config):
    _, config_path = propagate_config
    with pytest.raises(SystemExit):
        cmd_sync_propagate([
            "github",
            "--dry-run",
            "--smoke", "echo ok",
            "--smoke-preset", "provider-validate",
            "--config", str(config_path),
        ])

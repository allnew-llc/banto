# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for xAI phase-3 API key rotation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.cli import cmd_sync_xai_api_key
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.sync import SyncReport
from banto.sync.xai_rotator import (
    CreatedXAIAPIKey,
    DeletedXAIAPIKey,
    XAIPropagationStatus,
    XAIRotatorError,
    build_xai_api_key_plan,
    default_xai_key_name,
    resolve_xai_management_key,
    rotate_xai_api_key,
)


@pytest.fixture
def xai_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="xai",
        account="xai",
        env_name="XAI_API_KEY",
        targets=[Target(
            platform="vercel",
            project="moshimoshi-genki-xai-voice-gateway",
            environments=["production", "preview"],
        )],
    ))
    config.add_secret(SecretEntry(
        name="github",
        account="github",
        env_name="GITHUB_TOKEN",
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


def _propagation_result(config: SyncConfig, secret_name: str, *, ok: bool, version: int = 3) -> PropagationResult:
    plan = build_propagation_plan(config, secret_name)
    report = SyncReport()
    if not ok:
        report.results.append(MagicMock(
            secret_name=secret_name,
            target_label="vercel:voice-gateway",
            success=False,
            message="boom",
        ))
    return PropagationResult(
        plan=plan,
        stored=True,
        version=version,
        sync_report=report,
    )


def test_build_xai_api_key_plan(xai_config):
    config, _ = xai_config
    plan = build_xai_api_key_plan(config, "xai", "team_123")
    assert plan.team_id == "team_123"
    assert plan.propagation_plan.provider == "xai"
    assert plan.propagation_plan.rotation_class == "full_auto"
    assert "api-key:model:*" in plan.acls
    assert "api-key:endpoint:*" in plan.acls


def test_build_xai_api_key_plan_rejects_non_xai(xai_config):
    config, _ = xai_config
    with pytest.raises(ValueError):
        build_xai_api_key_plan(config, "github", "team_123")


def test_default_xai_key_name_is_ascii_safe():
    generated = default_xai_key_name("xAI Voice Key")
    assert generated.startswith("banto-xai-voice-key-")
    assert generated == generated.lower()


@patch.dict("os.environ", {"XAI_MANAGEMENT_API_KEY": "management-from-env"}, clear=False)
def test_resolve_xai_management_key_prefers_env(xai_config):
    config, _ = xai_config
    value, source = resolve_xai_management_key(config)
    assert value == "management-from-env"
    assert source == "env:XAI_MANAGEMENT_API_KEY"


@patch("banto.sync.xai_rotator.propagate_secret")
@patch("banto.sync.xai_rotator.create_xai_api_key")
@patch("banto.sync.xai_rotator.KeychainStore")
@patch.dict("os.environ", {"XAI_MANAGEMENT_API_KEY": "management-from-env"}, clear=False)
def test_rotate_xai_api_key_success(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    xai_config,
):
    config, _ = xai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedXAIAPIKey(
        api_key_id="ak_new",
        api_key_value="xai-new",
        name="banto-xai",
        redacted_api_key="xai-...new",
    )
    mock_propagate.return_value = _propagation_result(config, "xai", ok=True, version=5)

    result = rotate_xai_api_key(config, "xai", "team_123")

    assert result.ok is True
    assert result.created is not None
    assert result.created.api_key_id == "ak_new"
    mock_propagate.assert_called_once()
    assert mock_propagate.call_args.args[2] == "xai-new"


@patch("banto.sync.xai_rotator._safe_delete_xai_api_key")
@patch("banto.sync.xai_rotator.wait_for_xai_api_key_propagation")
@patch("banto.sync.xai_rotator.propagate_secret")
@patch("banto.sync.xai_rotator.create_xai_api_key")
@patch("banto.sync.xai_rotator.KeychainStore")
@patch.dict("os.environ", {"XAI_MANAGEMENT_API_KEY": "management-from-env"}, clear=False)
def test_rotate_xai_api_key_waits_for_propagation_before_storing(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_wait,
    mock_safe_delete,
    xai_config,
):
    config, _ = xai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedXAIAPIKey(
        api_key_id="ak_new",
        api_key_value="xai-new",
        name="banto-xai",
    )
    mock_wait.return_value = XAIPropagationStatus(
        api_key_id="ak_new",
        propagated=True,
        clusters={"ic-1": True},
    )
    mock_propagate.return_value = _propagation_result(config, "xai", ok=True, version=6)

    result = rotate_xai_api_key(
        config,
        "xai",
        "team_123",
        wait_for_propagation=True,
    )

    assert result.ok is True
    assert result.propagation_status is not None
    assert result.propagation_status.propagated is True
    mock_wait.assert_called_once()
    mock_safe_delete.assert_not_called()


@patch("banto.sync.xai_rotator._safe_delete_xai_api_key")
@patch("banto.sync.xai_rotator.propagate_secret")
@patch("banto.sync.xai_rotator.create_xai_api_key")
@patch("banto.sync.xai_rotator.KeychainStore")
@patch.dict("os.environ", {"XAI_MANAGEMENT_API_KEY": "management-from-env"}, clear=False)
def test_rotate_xai_api_key_rolls_back_and_cleans_up_on_failure(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    xai_config,
):
    config, _ = xai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedXAIAPIKey(
        api_key_id="ak_new",
        api_key_value="xai-new",
        name="banto-xai",
    )
    mock_propagate.side_effect = [
        _propagation_result(config, "xai", ok=False, version=6),
        _propagation_result(config, "xai", ok=True, version=7),
    ]
    mock_safe_delete.return_value = DeletedXAIAPIKey(api_key_id="ak_new", deleted=True)

    result = rotate_xai_api_key(config, "xai", "team_123")

    assert result.ok is False
    assert result.rollback is not None
    assert result.rollback.restored_previous_value is True
    assert result.cleanup_of_created_key is not None
    assert result.cleanup_of_created_key.deleted is True
    assert mock_propagate.call_count == 2
    assert mock_propagate.call_args_list[0].args[2] == "xai-new"
    assert mock_propagate.call_args_list[1].args[2] == "previous-key"


@patch("banto.sync.xai_rotator._safe_delete_xai_api_key")
@patch("banto.sync.xai_rotator.propagate_secret")
@patch("banto.sync.xai_rotator.create_xai_api_key")
@patch("banto.sync.xai_rotator.KeychainStore")
@patch.dict("os.environ", {"XAI_MANAGEMENT_API_KEY": "management-from-env"}, clear=False)
def test_rotate_xai_api_key_revokes_previous_key(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    xai_config,
):
    config, _ = xai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedXAIAPIKey(
        api_key_id="ak_new",
        api_key_value="xai-new",
        name="banto-xai",
    )
    mock_propagate.return_value = _propagation_result(config, "xai", ok=True, version=8)
    mock_safe_delete.return_value = DeletedXAIAPIKey(api_key_id="ak_old", deleted=True)

    result = rotate_xai_api_key(
        config,
        "xai",
        "team_123",
        revoke_api_key_id="ak_old",
    )

    assert result.ok is True
    assert result.revoked_previous_key is not None
    assert result.revoked_previous_key.deleted is True


@patch("banto.sync.cli.rotate_xai_api_key")
def test_cmd_sync_xai_api_key_dry_run(mock_rotate, xai_config, capsys):
    _, config_path = xai_config
    cmd_sync_xai_api_key([
        "xai",
        "--team-id", "team_123",
        "--wait-propagation",
        "--smoke-preset", "provider-validate",
        "--dry-run",
        "--config", str(config_path),
    ])
    out = capsys.readouterr().out
    assert "BANTO SYNC XAI API KEY" in out
    assert "team_123" in out
    assert "preset:provider-validate" in out
    mock_rotate.assert_not_called()


@patch("banto.sync.cli.rotate_xai_api_key")
def test_cmd_sync_xai_api_key_json_failure(mock_rotate, xai_config, capsys):
    _, config_path = xai_config
    mock_rotate.side_effect = XAIRotatorError("missing management key")

    with pytest.raises(SystemExit):
        cmd_sync_xai_api_key([
            "xai",
            "--team-id", "team_123",
            "--json",
            "--config", str(config_path),
        ])

    out = capsys.readouterr().out
    assert "missing management key" in out

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for OpenAI phase-3 service-account rotation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.cli import (
    cmd_sync_openai_service_account,
    cmd_sync_openai_service_accounts,
)
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.openai_rotator import (
    CreatedOpenAIServiceAccount,
    DeletedOpenAIServiceAccount,
    OpenAIRotatorError,
    build_openai_service_account_plan,
    default_service_account_name,
    list_project_service_accounts,
    resolve_openai_admin_key,
    rotate_openai_service_account,
)
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.sync import SyncReport


@pytest.fixture
def openai_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
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
        report.results.append(MagicMock(secret_name=secret_name, target_label="local:test", success=False, message="boom"))
    return PropagationResult(
        plan=plan,
        stored=True,
        version=version,
        sync_report=report,
    )


def test_build_openai_service_account_plan(openai_config):
    config, _ = openai_config
    plan = build_openai_service_account_plan(config, "openai", "proj_123")
    assert plan.project_id == "proj_123"
    assert plan.propagation_plan.provider == "openai"
    assert plan.propagation_plan.rotation_class == "full_auto"
    assert plan.service_account_name.startswith("banto-openai-")


def test_build_openai_service_account_plan_rejects_non_openai(openai_config):
    config, _ = openai_config
    with pytest.raises(ValueError):
        build_openai_service_account_plan(config, "github", "proj_123")


def test_default_service_account_name_is_ascii_safe():
    generated = default_service_account_name("OpenAI API Key")
    assert generated.startswith("banto-openai-api-key-")
    assert generated == generated.lower()


@patch("banto.sync.openai_rotator._openai_request_json")
def test_list_project_service_accounts_parses_rows(mock_request):
    mock_request.return_value = {
        "object": "list",
        "data": [
            {
                "id": "svc_old",
                "name": "banto-openai-older",
                "role": "member",
                "created_at": 1711471533,
            },
            {
                "id": "svc_new",
                "name": "banto-openai-newer",
                "role": "member",
                "created_at": 1711471633,
            },
        ],
    }

    rows = list_project_service_accounts("proj_123", admin_key="admin")

    assert [row.service_account_id for row in rows] == ["svc_old", "svc_new"]
    assert rows[0].service_account_name == "banto-openai-older"
    assert rows[1].created_at == 1711471633


@patch.dict("os.environ", {"OPENAI_ADMIN_KEY": "admin-from-env"}, clear=False)
def test_resolve_openai_admin_key_prefers_env(openai_config):
    config, _ = openai_config
    value, source = resolve_openai_admin_key(config)
    assert value == "admin-from-env"
    assert source == "env:OPENAI_ADMIN_KEY"


@patch("banto.sync.openai_rotator.propagate_secret")
@patch("banto.sync.openai_rotator.create_project_service_account")
@patch("banto.sync.openai_rotator.KeychainStore")
@patch.dict("os.environ", {"OPENAI_ADMIN_KEY": "admin-from-env"}, clear=False)
def test_rotate_openai_service_account_success(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    openai_config,
):
    config, _ = openai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedOpenAIServiceAccount(
        service_account_id="svc_new",
        service_account_name="banto-openai",
        api_key_id="key_new",
        api_key_value="sk-new",
    )
    mock_propagate.return_value = _propagation_result(config, "openai", ok=True, version=5)

    result = rotate_openai_service_account(config, "openai", "proj_123")

    assert result.ok is True
    assert result.created is not None
    assert result.created.service_account_id == "svc_new"
    mock_propagate.assert_called_once()


@patch("banto.sync.openai_rotator._safe_delete_project_service_account")
@patch("banto.sync.openai_rotator.propagate_secret")
@patch("banto.sync.openai_rotator.create_project_service_account")
@patch("banto.sync.openai_rotator.KeychainStore")
@patch.dict("os.environ", {"OPENAI_ADMIN_KEY": "admin-from-env"}, clear=False)
def test_rotate_openai_service_account_rolls_back_and_cleans_up_on_failure(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    openai_config,
):
    config, _ = openai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedOpenAIServiceAccount(
        service_account_id="svc_new",
        service_account_name="banto-openai",
        api_key_id="key_new",
        api_key_value="sk-new",
    )
    mock_propagate.side_effect = [
        _propagation_result(config, "openai", ok=False, version=6),
        _propagation_result(config, "openai", ok=True, version=7),
    ]
    mock_safe_delete.return_value = DeletedOpenAIServiceAccount(
        service_account_id="svc_new",
        deleted=True,
    )

    result = rotate_openai_service_account(config, "openai", "proj_123")

    assert result.ok is False
    assert result.rollback is not None
    assert result.rollback.attempted is True
    assert result.rollback.restored_previous_value is True
    assert result.cleanup_of_created_service_account is not None
    assert result.cleanup_of_created_service_account.deleted is True
    assert mock_propagate.call_count == 2
    assert mock_propagate.call_args_list[0].args[2] == "sk-new"
    assert mock_propagate.call_args_list[1].args[2] == "previous-key"


@patch("banto.sync.openai_rotator._safe_delete_project_service_account")
@patch("banto.sync.openai_rotator.propagate_secret")
@patch("banto.sync.openai_rotator.create_project_service_account")
@patch("banto.sync.openai_rotator.KeychainStore")
@patch.dict("os.environ", {"OPENAI_ADMIN_KEY": "admin-from-env"}, clear=False)
def test_rotate_openai_service_account_revokes_previous_service_account(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    openai_config,
):
    config, _ = openai_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedOpenAIServiceAccount(
        service_account_id="svc_new",
        service_account_name="banto-openai",
        api_key_id="key_new",
        api_key_value="sk-new",
    )
    mock_propagate.return_value = _propagation_result(config, "openai", ok=True, version=8)
    mock_safe_delete.return_value = DeletedOpenAIServiceAccount(
        service_account_id="svc_old",
        deleted=True,
    )

    result = rotate_openai_service_account(
        config,
        "openai",
        "proj_123",
        revoke_service_account_id="svc_old",
    )

    assert result.ok is True
    assert result.revoked_previous_service_account is not None
    assert result.revoked_previous_service_account.deleted is True


@patch("banto.sync.cli.rotate_openai_service_account")
def test_cmd_sync_openai_service_account_dry_run(mock_rotate, openai_config, capsys):
    _, config_path = openai_config
    cmd_sync_openai_service_account([
        "openai",
        "--project-id", "proj_123",
        "--smoke-preset", "provider-validate",
        "--dry-run",
        "--config", str(config_path),
    ])
    out = capsys.readouterr().out
    assert "BANTO SYNC OPENAI SERVICE ACCOUNT" in out
    assert "proj_123" in out
    assert "preset:provider-validate" in out
    mock_rotate.assert_not_called()


@patch("banto.sync.cli.rotate_openai_service_account")
def test_cmd_sync_openai_service_account_json_failure(mock_rotate, openai_config, capsys):
    _, config_path = openai_config
    mock_rotate.side_effect = OpenAIRotatorError("missing admin key")

    with pytest.raises(SystemExit):
        cmd_sync_openai_service_account([
            "openai",
            "--project-id", "proj_123",
            "--json",
            "--config", str(config_path),
        ])

    out = capsys.readouterr().out
    assert "missing admin key" in out


@patch("banto.sync.cli.list_project_service_accounts")
@patch("banto.sync.cli.resolve_openai_admin_key")
def test_cmd_sync_openai_service_accounts_lists_newest_first(
    mock_resolve,
    mock_list,
    openai_config,
    capsys,
):
    _, config_path = openai_config
    mock_resolve.return_value = ("admin-key", "keychain:test-sync:openai-admin")
    mock_list.return_value = [
        MagicMock(
            service_account_id="svc_old",
            service_account_name="banto-openai-older",
            role="member",
            created_at=1711471533,
        ),
        MagicMock(
            service_account_id="svc_new",
            service_account_name="banto-openai-newer",
            role="member",
            created_at=1711471633,
        ),
    ]

    cmd_sync_openai_service_accounts([
        "--project-id", "proj_123",
        "--config", str(config_path),
    ])

    out = capsys.readouterr().out
    assert "BANTO SYNC OPENAI SERVICE ACCOUNTS" in out
    assert out.index("svc_new") < out.index("svc_old")


@patch("banto.sync.cli.delete_project_service_account")
@patch("banto.sync.cli.resolve_openai_admin_key")
def test_cmd_sync_openai_revoke_service_account(
    mock_resolve,
    mock_delete,
    openai_config,
    capsys,
):
    _, config_path = openai_config
    mock_resolve.return_value = ("admin-key", "keychain:test-sync:openai-admin")
    mock_delete.return_value = DeletedOpenAIServiceAccount(
        service_account_id="svc_old",
        deleted=True,
    )

    from banto.sync.cli import cmd_sync_openai_revoke_service_account

    cmd_sync_openai_revoke_service_account([
        "--project-id", "proj_123",
        "--service-account-id", "svc_old",
        "--config", str(config_path),
    ])

    out = capsys.readouterr().out
    assert "Revoked OpenAI service account svc_old" in out

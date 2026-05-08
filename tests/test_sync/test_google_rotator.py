# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for Google phase-3 API key rotation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.cli import cmd_sync_google_api_key
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.google_rotator import (
    CreatedGoogleAPIKey,
    DeletedGoogleAPIKey,
    DEFAULT_GCLOUD_AUTH_COMMAND,
    GoogleRotatorError,
    build_google_api_key_plan,
    default_google_display_name,
    resolve_google_access_token,
    rotate_google_api_key,
)
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.sync import SyncReport


@pytest.fixture
def google_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="google-api-key",
        account="shared-google",
        env_name="GOOGLE_API_KEY",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config.add_secret(SecretEntry(
        name="gemini-api-key",
        account="shared-google",
        env_name="GEMINI_API_KEY",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars.gemini"))],
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


def test_build_google_api_key_plan(google_config):
    config, _ = google_config
    plan = build_google_api_key_plan(config, "google-api-key", "my-project")
    assert plan.project_id == "my-project"
    assert plan.parent == "projects/my-project/locations/global"
    assert plan.propagation_plan.provider == "google"
    assert plan.shared_account_secret_names == ("gemini-api-key",)


def test_build_google_api_key_plan_rejects_non_google(google_config):
    config, _ = google_config
    with pytest.raises(ValueError):
        build_google_api_key_plan(config, "github", "my-project")


def test_default_google_display_name_is_ascii_safe():
    generated = default_google_display_name("Google API Key")
    assert generated.startswith("banto-google-api-key-")
    assert generated == generated.lower()


@patch.dict("os.environ", {"GOOGLE_OAUTH_ACCESS_TOKEN": "access-token"}, clear=False)
def test_resolve_google_access_token_prefers_env():
    value, source = resolve_google_access_token()
    assert value == "access-token"
    assert source == "env:GOOGLE_OAUTH_ACCESS_TOKEN"


@patch("banto.sync.google_rotator.subprocess.run")
def test_resolve_google_access_token_falls_back_to_adc(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="adc-token\n", stderr="")
    value, source = resolve_google_access_token(env_var="UNSET_GOOGLE_TOKEN")
    assert value == "adc-token"
    assert source.startswith("adc:")


@patch("banto.sync.google_rotator.subprocess.run")
def test_resolve_google_access_token_falls_back_to_gcloud_auth(mock_run):
    mock_run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="adc missing"),
        MagicMock(returncode=0, stdout="user-token\n", stderr=""),
    ]
    value, source = resolve_google_access_token(env_var="UNSET_GOOGLE_TOKEN")
    assert value == "user-token"
    assert source == f"gcloud:{DEFAULT_GCLOUD_AUTH_COMMAND}"


@patch("banto.sync.google_rotator.propagate_secret")
@patch("banto.sync.google_rotator.create_google_api_key")
@patch("banto.sync.google_rotator.KeychainStore")
@patch.dict("os.environ", {"GOOGLE_OAUTH_ACCESS_TOKEN": "access-token"}, clear=False)
def test_rotate_google_api_key_success(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    google_config,
):
    config, _ = google_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        display_name="banto-google-api-key",
        key_id="new-key",
        key_uid="uid-1",
        key_string="new-secret",
        operation_name="operations/create-key",
    )
    mock_propagate.return_value = _propagation_result(config, "google-api-key", ok=True, version=5)

    result = rotate_google_api_key(config, "google-api-key", "my-project")

    assert result.ok is True
    assert result.created is not None
    assert result.created.key_name.endswith("/new-key")
    mock_propagate.assert_called_once()


@patch("banto.sync.google_rotator._safe_delete_google_api_key")
@patch("banto.sync.google_rotator.propagate_secret")
@patch("banto.sync.google_rotator.create_google_api_key")
@patch("banto.sync.google_rotator.KeychainStore")
@patch.dict("os.environ", {"GOOGLE_OAUTH_ACCESS_TOKEN": "access-token"}, clear=False)
def test_rotate_google_api_key_rolls_back_and_cleans_up_on_primary_failure(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    google_config,
):
    config, _ = google_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        display_name="banto-google-api-key",
        key_id="new-key",
        key_uid="uid-1",
        key_string="new-secret",
        operation_name="operations/create-key",
    )
    mock_propagate.side_effect = [
        _propagation_result(config, "google-api-key", ok=False, version=6),
        _propagation_result(config, "google-api-key", ok=True, version=7),
    ]
    mock_safe_delete.return_value = DeletedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        deleted=True,
        operation_name="operations/delete-key",
    )

    result = rotate_google_api_key(config, "google-api-key", "my-project")

    assert result.ok is False
    assert len(result.rollback_entries) == 1
    assert result.rollback_entries[0].restored_previous_value is True
    assert result.cleanup_of_created_key is not None
    assert result.cleanup_of_created_key.deleted is True
    assert mock_propagate.call_count == 2


@patch("banto.sync.google_rotator._safe_delete_google_api_key")
@patch("banto.sync.google_rotator.propagate_secret")
@patch("banto.sync.google_rotator.create_google_api_key")
@patch("banto.sync.google_rotator.KeychainStore")
@patch.dict("os.environ", {"GOOGLE_OAUTH_ACCESS_TOKEN": "access-token"}, clear=False)
def test_rotate_google_api_key_syncs_shared_account_sibling(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    google_config,
):
    config, _ = google_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        display_name="banto-google-api-key",
        key_id="new-key",
        key_uid="uid-1",
        key_string="new-secret",
        operation_name="operations/create-key",
    )
    mock_propagate.side_effect = [
        _propagation_result(config, "google-api-key", ok=True, version=8),
        _propagation_result(config, "gemini-api-key", ok=True, version=9),
    ]

    result = rotate_google_api_key(
        config,
        "google-api-key",
        "my-project",
        sync_shared_account_secrets=True,
    )

    assert result.ok is True
    assert len(result.sibling_propagations) == 1
    assert result.sibling_propagations[0].secret_name == "gemini-api-key"
    assert result.sibling_propagations[0].ok is True
    mock_safe_delete.assert_not_called()


@patch("banto.sync.google_rotator._safe_delete_google_api_key")
@patch("banto.sync.google_rotator.propagate_secret")
@patch("banto.sync.google_rotator.create_google_api_key")
@patch("banto.sync.google_rotator.KeychainStore")
@patch.dict("os.environ", {"GOOGLE_OAUTH_ACCESS_TOKEN": "access-token"}, clear=False)
def test_rotate_google_api_key_rolls_back_when_sibling_sync_fails(
    mock_kc_cls,
    mock_create,
    mock_propagate,
    mock_safe_delete,
    google_config,
):
    config, _ = google_config
    mock_kc_cls.return_value.get.return_value = "previous-key"
    mock_create.return_value = CreatedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        display_name="banto-google-api-key",
        key_id="new-key",
        key_uid="uid-1",
        key_string="new-secret",
        operation_name="operations/create-key",
    )
    mock_propagate.side_effect = [
        _propagation_result(config, "google-api-key", ok=True, version=8),
        _propagation_result(config, "gemini-api-key", ok=False, version=9),
        _propagation_result(config, "google-api-key", ok=True, version=10),
    ]
    mock_safe_delete.return_value = DeletedGoogleAPIKey(
        key_name="projects/my-project/locations/global/keys/new-key",
        deleted=True,
        operation_name="operations/delete-key",
    )

    result = rotate_google_api_key(
        config,
        "google-api-key",
        "my-project",
        sync_shared_account_secrets=True,
    )

    assert result.ok is False
    assert len(result.rollback_entries) == 1
    assert result.rollback_entries[0].secret_name == "google-api-key"
    assert result.cleanup_of_created_key is not None
    assert result.cleanup_of_created_key.deleted is True


@patch("banto.sync.cli.rotate_google_api_key")
def test_cmd_sync_google_api_key_dry_run(mock_rotate, google_config, capsys):
    _, config_path = google_config
    cmd_sync_google_api_key([
        "google-api-key",
        "--project-id", "my-project",
        "--smoke-preset", "provider-validate",
        "--dry-run",
        "--config", str(config_path),
    ])
    out = capsys.readouterr().out
    assert "BANTO SYNC GOOGLE API KEY" in out
    assert "my-project" in out
    assert "gemini-api-key" in out
    assert "preset:provider-validate" in out
    mock_rotate.assert_not_called()


@patch("banto.sync.cli.rotate_google_api_key")
def test_cmd_sync_google_api_key_json_failure(mock_rotate, google_config, capsys):
    _, config_path = google_config
    mock_rotate.side_effect = GoogleRotatorError("missing adc")

    with pytest.raises(SystemExit):
        cmd_sync_google_api_key([
            "google-api-key",
            "--project-id", "my-project",
            "--json",
            "--config", str(config_path),
        ])

    out = capsys.readouterr().out
    assert "missing adc" in out

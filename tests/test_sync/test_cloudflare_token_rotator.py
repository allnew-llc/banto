# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for Cloudflare Account API token issuance."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from banto.sync.cli import cmd_sync_cloudflare_account_token
from banto.sync.cloudflare_token_rotator import (
    CreatedCloudflareToken,
    DeletedCloudflareToken,
    build_cloudflare_account_token_plan,
    load_cloudflare_token_policy,
    rotate_cloudflare_account_token,
)
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.sync import SyncReport


@pytest.fixture
def cf_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="cloudflare-api-token",
        account="cloudflare-api-token",
        env_name="CLOUDFLARE_API_TOKEN",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config.add_secret(SecretEntry(name="github", account="github", env_name="GITHUB_TOKEN"))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


def _policy() -> dict:
    return {
        "policies": [
            {
                "effect": "allow",
                "resources": {"com.cloudflare.api.account.abc": "*"},
                "permission_groups": [{"id": "pg_123", "name": "Workers Scripts Write"}],
            }
        ]
    }


def _policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "cf-policy.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")
    return path


def _propagation_result(config: SyncConfig) -> PropagationResult:
    return PropagationResult(
        plan=build_propagation_plan(config, "cloudflare-api-token"),
        stored=True,
        version=2,
        sync_report=SyncReport(),
    )


def test_load_cloudflare_token_policy_accepts_object(tmp_path):
    loaded = load_cloudflare_token_policy(_policy_file(tmp_path))
    assert len(loaded["policies"]) == 1


def test_build_cloudflare_plan_rejects_non_cloudflare(cf_config):
    config, _ = cf_config
    with pytest.raises(ValueError):
        build_cloudflare_account_token_plan(config, "github", "account_id", _policy())


@patch("banto.sync.cloudflare_token_rotator.propagate_secret")
@patch("banto.sync.cloudflare_token_rotator.delete_cloudflare_account_token")
@patch("banto.sync.cloudflare_token_rotator.create_cloudflare_account_token")
@patch.dict("os.environ", {"CLOUDFLARE_TOKEN_CREATOR_API_TOKEN": "creator-token"}, clear=False)
def test_rotate_cloudflare_token_propagates_and_revokes_without_returning_value(
    mock_create,
    mock_delete,
    mock_propagate,
    cf_config,
):
    config, _ = cf_config
    mock_create.return_value = CreatedCloudflareToken(
        token_id="tok_123",
        token_name="banto-cf",
        token_value="cf-secret-value",
    )
    mock_delete.return_value = DeletedCloudflareToken(token_id="tok_old", deleted=True)
    mock_propagate.return_value = _propagation_result(config)

    result = rotate_cloudflare_account_token(
        config,
        "cloudflare-api-token",
        "account_id",
        _policy(),
        revoke_token_id="tok_old",
    )

    assert result.ok is True
    assert "cf-secret-value" not in repr(result)
    mock_propagate.assert_called_once()
    assert mock_propagate.call_args.args[2] == "cf-secret-value"
    mock_delete.assert_called_once()
    assert result.revoked_previous_token is not None
    assert result.revoked_previous_token.deleted is True


@patch("banto.sync.cli.rotate_cloudflare_account_token")
def test_cmd_cloudflare_account_token_dry_run(mock_rotate, cf_config, tmp_path, capsys):
    _, config_path = cf_config
    policy_path = _policy_file(tmp_path)

    cmd_sync_cloudflare_account_token([
        "cloudflare-api-token",
        "--account-id", "account_id",
        "--policy-file", str(policy_path),
        "--revoke-token", "tok_old",
        "--dry-run",
        "--config", str(config_path),
    ])

    out = capsys.readouterr().out
    assert "BANTO SYNC CLOUDFLARE ACCOUNT TOKEN" in out
    assert "cloudflare-api-token" in out
    assert "tok_old" in out
    mock_rotate.assert_not_called()


def test_cmd_cloudflare_account_token_help_exits_cleanly(capsys):
    cmd_sync_cloudflare_account_token(["--help"])
    out = capsys.readouterr().out
    assert "Usage: banto sync cloudflare-account-token" in out

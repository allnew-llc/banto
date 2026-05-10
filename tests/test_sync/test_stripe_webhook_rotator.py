# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for Stripe webhook endpoint issuance."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.cli import cmd_sync_stripe_webhook_endpoint
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.stripe_webhook_rotator import (
    CreatedStripeWebhookEndpoint,
    DeletedStripeWebhookEndpoint,
    StripeWebhookRotatorError,
    build_stripe_webhook_endpoint_plan,
    create_stripe_webhook_endpoint_with_cli,
    rotate_stripe_webhook_endpoint,
)
from banto.sync.sync import SyncReport


@pytest.fixture
def stripe_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="stripe-test-secret",
        account="stripe-test-secret",
        env_name="STRIPE_SECRET_KEY",
    ))
    config.add_secret(SecretEntry(
        name="stripe-test-webhook",
        account="stripe-test-webhook",
        env_name="STRIPE_WEBHOOK_SECRET",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config.add_secret(SecretEntry(name="github", account="github", env_name="GITHUB_TOKEN"))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


def _propagation_result(config: SyncConfig) -> PropagationResult:
    return PropagationResult(
        plan=build_propagation_plan(config, "stripe-test-webhook"),
        stored=True,
        version=3,
        sync_report=SyncReport(),
    )


def test_build_stripe_webhook_plan(stripe_config):
    config, _ = stripe_config
    plan = build_stripe_webhook_endpoint_plan(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
    )

    assert plan.source_secret_name == "stripe-test-secret"
    assert plan.propagation_plan.rotation_class == "manual_cutover"


def test_build_stripe_webhook_plan_rejects_non_webhook(stripe_config):
    config, _ = stripe_config
    with pytest.raises(ValueError):
        build_stripe_webhook_endpoint_plan(
            config,
            "github",
            source_secret_name="stripe-test-secret",
            url="https://example.com/api/stripe/webhook",
            enabled_events=("checkout.session.completed",),
        )


@patch("banto.sync.stripe_webhook_rotator.subprocess.run")
def test_create_stripe_webhook_endpoint_with_cli_parses_prefixed_json(mock_run, stripe_config):
    config, _ = stripe_config
    plan = build_stripe_webhook_endpoint_plan(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
    )
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "This command will be executed on the account with the following details:\n"
            '{"id":"we_cli","secret":"whsec_cli_secret","livemode":true,"status":"enabled"}\n'
        ),
    )

    result = create_stripe_webhook_endpoint_with_cli(plan, live_mode=True)

    assert result.endpoint_id == "we_cli"
    assert result.signing_secret == "whsec_cli_secret"
    assert result.livemode is True
    argv = mock_run.call_args.args[0]
    assert "--live" in argv
    assert "checkout.session.completed" in argv


@patch("banto.sync.stripe_webhook_rotator.subprocess.run")
def test_create_stripe_webhook_endpoint_with_cli_redacts_error_key_fragments(
    mock_run,
    stripe_config,
):
    config, _ = stripe_config
    plan = build_stripe_webhook_endpoint_plan(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
    )
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"error":{"message":"Invalid API Key provided: rk_live_abc123XYZ"}}',
    )

    with pytest.raises(StripeWebhookRotatorError) as exc:
        create_stripe_webhook_endpoint_with_cli(plan, live_mode=True)

    message = str(exc.value)
    assert "rk_live_[redacted]" in message
    assert "abc123XYZ" not in message


@patch("banto.sync.stripe_webhook_rotator.propagate_secret")
@patch("banto.sync.stripe_webhook_rotator.delete_stripe_webhook_endpoint")
@patch("banto.sync.stripe_webhook_rotator.create_stripe_webhook_endpoint")
@patch("banto.sync.stripe_webhook_rotator.KeychainStore")
def test_rotate_stripe_webhook_propagates_and_deletes_previous_without_returning_secret(
    mock_kc_cls,
    mock_create,
    mock_delete,
    mock_propagate,
    stripe_config,
):
    config, _ = stripe_config
    mock_kc_cls.return_value.get.return_value = "sk_test_source"
    mock_create.return_value = CreatedStripeWebhookEndpoint(
        endpoint_id="we_123",
        signing_secret="whsec_new_secret",
    )
    mock_delete.return_value = DeletedStripeWebhookEndpoint(endpoint_id="we_old", deleted=True)
    mock_propagate.return_value = _propagation_result(config)

    result = rotate_stripe_webhook_endpoint(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
        delete_previous_endpoint_id="we_old",
    )

    assert result.ok is True
    assert "whsec_new_secret" not in repr(result)
    mock_propagate.assert_called_once()
    assert mock_propagate.call_args.args[2] == "whsec_new_secret"
    assert mock_propagate.call_args.kwargs["allow_manual_cutover"] is True
    mock_delete.assert_called_once()
    assert result.deleted_previous_endpoint is not None
    assert result.deleted_previous_endpoint.deleted is True


@patch("banto.sync.stripe_webhook_rotator.propagate_secret")
@patch("banto.sync.stripe_webhook_rotator.delete_stripe_webhook_endpoint_with_cli")
@patch("banto.sync.stripe_webhook_rotator.create_stripe_webhook_endpoint_with_cli")
def test_rotate_stripe_webhook_uses_cli_auth_when_requested(
    mock_create_cli,
    mock_delete_cli,
    mock_propagate,
    stripe_config,
):
    config, _ = stripe_config
    mock_create_cli.return_value = CreatedStripeWebhookEndpoint(
        endpoint_id="we_cli",
        signing_secret="whsec_cli_secret",
        livemode=True,
    )
    mock_delete_cli.return_value = DeletedStripeWebhookEndpoint(endpoint_id="we_old", deleted=True)
    mock_propagate.return_value = _propagation_result(config)

    result = rotate_stripe_webhook_endpoint(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
        delete_previous_endpoint_id="we_old",
        use_stripe_cli_auth=True,
        stripe_cli_live_mode=True,
    )

    assert result.ok is True
    assert result.stripe_key_source == "stripe-cli:live"
    mock_create_cli.assert_called_once()
    assert mock_create_cli.call_args.kwargs["live_mode"] is True
    mock_delete_cli.assert_called_once_with("we_old", live_mode=True)
    assert mock_propagate.call_args.kwargs["allow_manual_cutover"] is True


@patch("banto.sync.stripe_webhook_rotator.propagate_secret")
@patch("banto.sync.stripe_webhook_rotator.delete_stripe_webhook_endpoint")
@patch("banto.sync.stripe_webhook_rotator.create_stripe_webhook_endpoint")
@patch("banto.sync.stripe_webhook_rotator.KeychainStore")
def test_rotate_stripe_webhook_cleans_created_endpoint_when_propagation_raises(
    mock_kc_cls,
    mock_create,
    mock_delete,
    mock_propagate,
    stripe_config,
):
    config, _ = stripe_config
    mock_kc_cls.return_value.get.return_value = "sk_test_source"
    mock_create.return_value = CreatedStripeWebhookEndpoint(
        endpoint_id="we_new",
        signing_secret="whsec_new_secret",
    )
    mock_propagate.side_effect = ValueError("manual cutover")
    mock_delete.return_value = DeletedStripeWebhookEndpoint(endpoint_id="we_new", deleted=True)

    result = rotate_stripe_webhook_endpoint(
        config,
        "stripe-test-webhook",
        source_secret_name="stripe-test-secret",
        url="https://example.com/api/stripe/webhook",
        enabled_events=("checkout.session.completed",),
    )

    assert result.ok is False
    assert result.propagation is None
    assert result.cleanup_of_created_endpoint is not None
    assert result.cleanup_of_created_endpoint.deleted is True
    assert result.error is not None
    assert "ValueError" in result.error
    mock_delete.assert_called_once_with("we_new", api_key="sk_test_source")


@patch("banto.sync.cli.rotate_stripe_webhook_endpoint")
def test_cmd_stripe_webhook_endpoint_dry_run(mock_rotate, stripe_config, capsys):
    _, config_path = stripe_config

    cmd_sync_stripe_webhook_endpoint([
        "stripe-test-webhook",
        "--source-secret", "stripe-test-secret",
        "--url", "https://example.com/api/stripe/webhook",
        "--event", "checkout.session.completed",
        "--delete-previous-endpoint", "we_old",
        "--dry-run",
        "--config", str(config_path),
    ])

    out = capsys.readouterr().out
    assert "BANTO SYNC STRIPE WEBHOOK ENDPOINT" in out
    assert "manual" in out.lower()
    assert "we_old" in out
    mock_rotate.assert_not_called()


def test_cmd_stripe_webhook_endpoint_help_exits_cleanly(capsys):
    cmd_sync_stripe_webhook_endpoint(["--help"])
    out = capsys.readouterr().out
    assert "Usage: banto sync stripe-webhook-endpoint" in out

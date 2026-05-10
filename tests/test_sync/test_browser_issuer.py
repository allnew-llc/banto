# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for browser-assisted credential issuance."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from banto.sync.browser_issuer import (
    BrowserCaptureResult,
    BrowserIssuerError,
    browser_retirement_recipe_from_dict,
    browser_recipe_from_dict,
    build_browser_issue_plan,
    issue_secret_with_browser,
    retire_key_with_browser,
)
from banto.sync.browser_recorder import (
    build_browser_recording_plan,
    recipe_dict_from_recorded_actions,
    record_browser_recipe,
)
from banto.sync.cli import (
    cmd_sync_browser_issue,
    cmd_sync_browser_record,
    cmd_sync_browser_revoke,
)
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.propagation import PropagationResult, build_propagation_plan
from banto.sync.sync import SyncReport


@pytest.fixture
def browser_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="github",
        account="github",
        env_name="GITHUB_TOKEN",
        targets=[Target(platform="local", file=str(tmp_path / ".dev.vars"))],
    ))
    config.add_secret(SecretEntry(
        name="hmac",
        account="hmac",
        env_name="HMAC_SECRET",
    ))
    config_path = tmp_path / "sync.json"
    config.save(config_path)
    return config, config_path


def _recipe_dict(provider: str = "github") -> dict:
    return {
        "version": 1,
        "name": "github-token-console",
        "provider": provider,
        "start_url": "https://github.com/settings/tokens",
        "steps": [
            {"action": "click", "selector": "text=Generate new token"},
            {"action": "fill", "selector": "input[name=description]", "text": "banto-{{timestamp}}"},
            {"action": "wait_for_selector", "selector": "[data-testid=issued-token]"},
        ],
        "capture": {
            "selector": "[data-testid=issued-token]",
            "source": "text",
            "min_length": 8,
        },
        "metadata_selectors": {
            "key_label": "[data-testid=issued-token-label]",
        },
    }


def _recipe_file(tmp_path: Path, provider: str = "github") -> Path:
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(_recipe_dict(provider)), encoding="utf-8")
    return path


def _retirement_recipe_dict(provider: str = "github") -> dict:
    return {
        "version": 1,
        "name": "github-token-retire",
        "provider": provider,
        "start_url": "https://github.com/settings/tokens",
        "steps": [
            {"action": "fill", "selector": "input[name=q]", "text": "{{key_id}}"},
            {"action": "click", "selector": "[data-token-id=\"{{key_id}}\"] button"},
            {"action": "click", "selector": "button:has-text('Delete')"},
        ],
        "success_selector": "[data-token-deleted=\"{{key_id}}\"]",
    }


def _retirement_recipe_file(tmp_path: Path, provider: str = "github") -> Path:
    path = tmp_path / "retire.json"
    path.write_text(json.dumps(_retirement_recipe_dict(provider)), encoding="utf-8")
    return path


def _propagation_result(config: SyncConfig, secret_name: str) -> PropagationResult:
    return PropagationResult(
        plan=build_propagation_plan(config, secret_name),
        stored=True,
        version=9,
        sync_report=SyncReport(),
    )


def test_browser_recipe_from_dict_validates_minimum_shape():
    recipe = browser_recipe_from_dict(_recipe_dict())

    assert recipe.provider == "github"
    assert recipe.capture.selector == "[data-testid=issued-token]"
    assert recipe.steps[1].text == "banto-{{timestamp}}"


def test_browser_recipe_rejects_non_https_start_url():
    raw = _recipe_dict()
    raw["start_url"] = "http://example.com/settings/tokens"

    with pytest.raises(BrowserIssuerError):
        browser_recipe_from_dict(raw)


def test_build_browser_issue_plan_rejects_provider_mismatch(browser_config):
    config, _ = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict(provider="stripe"))

    with pytest.raises(BrowserIssuerError):
        build_browser_issue_plan(config, "github", recipe)


def test_build_browser_issue_plan_rejects_manual_cutover(browser_config):
    config, _ = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict(provider="*"))

    with pytest.raises(ValueError):
        build_browser_issue_plan(config, "hmac", recipe)


@patch("banto.sync.browser_issuer.propagate_secret")
@patch("banto.sync.browser_issuer._run_playwright_recipe")
def test_issue_secret_with_browser_never_returns_secret_value(
    mock_run,
    mock_propagate,
    browser_config,
):
    config, _ = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict())
    mock_run.return_value = BrowserCaptureResult(
        secret_value="ghp_new_token_value",
        metadata={"key_label": "banto-github"},
    )
    mock_propagate.return_value = _propagation_result(config, "github")

    result = issue_secret_with_browser(config, "github", recipe, do_validate=True)

    assert result.ok is True
    assert result.metadata == {"key_label": "banto-github"}
    assert "ghp_new_token_value" not in repr(result)
    mock_propagate.assert_called_once()
    assert mock_propagate.call_args.args[2] == "ghp_new_token_value"


@patch("banto.sync.browser_issuer._run_playwright_retirement")
@patch("banto.sync.browser_issuer.propagate_secret")
@patch("banto.sync.browser_issuer._run_playwright_recipe")
def test_issue_secret_with_browser_retires_exposed_key_after_propagation(
    mock_run,
    mock_propagate,
    mock_retire,
    browser_config,
):
    config, _ = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict())
    retire_recipe = browser_retirement_recipe_from_dict(_retirement_recipe_dict())
    mock_run.return_value = BrowserCaptureResult(
        secret_value="ghp_new_token_value",
        metadata={"key_label": "banto-github"},
    )
    mock_propagate.return_value = _propagation_result(config, "github")

    result = issue_secret_with_browser(
        config,
        "github",
        recipe,
        retire_recipe=retire_recipe,
        retire_key_id="tok_old_123",
    )

    assert result.ok is True
    assert result.retirement is not None
    assert result.retirement.plan.key_id == "tok_old_123"
    mock_retire.assert_called_once()


@patch("banto.sync.browser_issuer._run_playwright_retirement")
@patch("banto.sync.browser_issuer.propagate_secret")
@patch("banto.sync.browser_issuer._run_playwright_recipe")
def test_issue_secret_with_browser_does_not_retire_when_propagation_fails(
    mock_run,
    mock_propagate,
    mock_retire,
    browser_config,
):
    config, _ = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict())
    retire_recipe = browser_retirement_recipe_from_dict(_retirement_recipe_dict())
    mock_run.return_value = BrowserCaptureResult(
        secret_value="ghp_new_token_value",
        metadata={},
    )
    mock_propagate.return_value = PropagationResult(
        plan=build_propagation_plan(config, "github"),
        stored=False,
        version=None,
        sync_report=None,
    )

    result = issue_secret_with_browser(
        config,
        "github",
        recipe,
        retire_recipe=retire_recipe,
        retire_key_id="tok_old_123",
    )

    assert result.ok is False
    assert result.retirement is None
    mock_retire.assert_not_called()


@patch("banto.sync.browser_issuer._run_playwright_retirement")
def test_retire_key_with_browser_rejects_raw_secret_identifiers(mock_retire, browser_config):
    config, _ = browser_config
    recipe = browser_retirement_recipe_from_dict(_retirement_recipe_dict())

    with pytest.raises(BrowserIssuerError):
        retire_key_with_browser(
            config,
            "github",
            recipe,
            key_id="ghp_raw_secret_value",
        )

    mock_retire.assert_not_called()


def test_recording_plan_generates_recipe_from_recorded_actions(browser_config, tmp_path):
    config, _ = browser_config
    plan = build_browser_recording_plan(
        config,
        "github",
        start_url="https://github.com/settings/tokens",
        output_path=tmp_path / "recipe.json",
        capture_from_last_click=True,
        metadata_selectors={"key_id": "[data-token-id]"},
    )

    recipe, warnings = recipe_dict_from_recorded_actions(
        plan,
        [
            {"action": "click", "selector": "text=Generate new token"},
            {"action": "fill", "selector": "input[name=description]", "text": "banto-test"},
            {"action": "fill", "selector": "input[name=token]", "sensitive": True},
            {"action": "click", "selector": "[data-testid=issued-token]"},
        ],
    )

    rendered = json.dumps(recipe)
    assert recipe["capture"]["selector"] == "[data-testid=issued-token]"
    assert recipe["metadata_selectors"] == {"key_id": "[data-token-id]"}
    assert "ghp_raw_secret_value" not in rendered
    assert any("sensitive fill" in warning for warning in warnings)


@patch("banto.sync.browser_recorder._run_playwright_recorder")
def test_record_browser_recipe_writes_recipe_script_and_manifest(
    mock_recorder,
    browser_config,
    tmp_path,
):
    config, _ = browser_config
    revoke_path = _retirement_recipe_file(tmp_path)
    plan = build_browser_recording_plan(
        config,
        "github",
        start_url="https://github.com/settings/tokens",
        output_path=tmp_path / "recipe.json",
        script_out=tmp_path / "run.sh",
        capture_selector="[data-testid=issued-token]",
        exposed_key_id="tok_old_123",
        exposure_manifest_out=tmp_path / "manifest.json",
        revoke_recipe_path=revoke_path,
    )
    mock_recorder.return_value = (
        [{"action": "click", "selector": "text=Generate new token"}],
        {"key_id": "tok_old_123"},
    )

    result = record_browser_recipe(plan)

    assert result.ok is True
    assert result.recipe_path.exists()
    assert result.script_path is not None and result.script_path.exists()
    script = result.script_path.read_text(encoding="utf-8")
    assert "--exposure-manifest" in script
    assert result.exposure_manifest_path is not None and result.exposure_manifest_path.exists()
    manifest = json.loads(result.exposure_manifest_path.read_text(encoding="utf-8"))
    assert manifest["key_id"] == "tok_old_123"
    assert manifest["revoke_recipe"] == str(revoke_path)


@patch("banto.sync.cli.issue_secret_with_browser")
def test_cmd_sync_browser_issue_dry_run(mock_issue, browser_config, tmp_path, capsys):
    _, config_path = browser_config
    recipe_path = _recipe_file(tmp_path)

    cmd_sync_browser_issue([
        "github",
        "--recipe", str(recipe_path),
        "--dry-run",
        "--config", str(config_path),
    ])

    out = capsys.readouterr().out
    assert "BANTO SYNC BROWSER ISSUE" in out
    assert "No browser was launched" in out
    mock_issue.assert_not_called()


@patch("banto.sync.cli.issue_secret_with_browser")
def test_cmd_sync_browser_issue_json_redacts_secret(mock_issue, browser_config, tmp_path, capsys):
    config, config_path = browser_config
    recipe = browser_recipe_from_dict(_recipe_dict())
    plan = build_browser_issue_plan(config, "github", recipe)
    mock_issue.return_value = MagicMock(
        ok=True,
        plan=plan,
        metadata={"key_label": "banto-github"},
        propagation=_propagation_result(config, "github"),
        error=None,
    )
    recipe_path = _recipe_file(tmp_path)

    cmd_sync_browser_issue([
        "github",
        "--recipe", str(recipe_path),
        "--json",
        "--config", str(config_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["metadata"] == {"key_label": "banto-github"}
    assert "ghp_new_token_value" not in json.dumps(payload)


@patch("banto.sync.cli.issue_secret_with_browser")
def test_cmd_sync_browser_issue_dry_run_accepts_exposure_manifest(
    mock_issue,
    browser_config,
    tmp_path,
    capsys,
):
    _, config_path = browser_config
    recipe_path = _recipe_file(tmp_path)
    revoke_path = _retirement_recipe_file(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "version": 1,
        "key_id": "tok_old_123",
        "revoke_recipe": str(revoke_path),
    }), encoding="utf-8")

    cmd_sync_browser_issue([
        "github",
        "--recipe", str(recipe_path),
        "--exposure-manifest", str(manifest_path),
        "--dry-run",
        "--json",
        "--config", str(config_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["retirement"]["key_id"] == "tok_old_123"
    mock_issue.assert_not_called()


@patch("banto.sync.cli.record_browser_recipe")
def test_cmd_sync_browser_record_dry_run(mock_record, browser_config, tmp_path, capsys):
    _, config_path = browser_config

    cmd_sync_browser_record([
        "github",
        "--start-url", "https://github.com/settings/tokens",
        "--output", str(tmp_path / "recipe.json"),
        "--capture-selector", "[data-testid=issued-token]",
        "--metadata", "key_id=[data-token-id]",
        "--dry-run",
        "--json",
        "--config", str(config_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["provider"] == "github"
    assert payload["metadata_keys"] == ["key_id"]
    mock_record.assert_not_called()


@patch("banto.sync.cli.retire_key_with_browser")
def test_cmd_sync_browser_revoke_dry_run(mock_retire, browser_config, tmp_path, capsys):
    _, config_path = browser_config
    revoke_path = _retirement_recipe_file(tmp_path)

    cmd_sync_browser_revoke([
        "github",
        "--recipe", str(revoke_path),
        "--key-id", "tok_old_123",
        "--dry-run",
        "--json",
        "--config", str(config_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["key_id"] == "tok_old_123"
    mock_retire.assert_not_called()

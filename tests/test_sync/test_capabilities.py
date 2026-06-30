# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for rotation capability classification."""
from __future__ import annotations

import json
from pathlib import Path

from banto.sync.capabilities import (
    ROTATION_CLASS_ORDER,
    classify_config,
    classify_secret,
    load_capability_matrix,
    summarize_classifications,
)
from banto.sync.cli import cmd_sync_classify
from banto.sync.config import SecretEntry, SyncConfig


def test_load_capability_matrix_has_expected_classes():
    matrix = load_capability_matrix()
    assert set(ROTATION_CLASS_ORDER).issubset(matrix.rotation_classes)
    assert any(rule.rule_id == "openai_api_key" for rule in matrix.rules)


def test_classify_secret_known_and_unknown_cases():
    openai = classify_secret("openai", "OPENAI_API_KEY")
    assert openai.provider == "openai"
    assert openai.rotation_class == "full_auto"

    xai = classify_secret("xai", "XAI_API_KEY")
    assert xai.provider == "xai"
    assert xai.rotation_class == "full_auto"

    line_id = classify_secret("line-owner", "LINE_OWNER_USER_ID")
    assert line_id.rotation_class == "inventory_only"

    webhook = classify_secret("stripe-webhook", "STRIPE_WEBHOOK_SECRET")
    assert webhook.rotation_class == "manual_cutover"

    poipoi_hmac = classify_secret("poipoi-hmac", "BAAS_FACTORY_HMAC_SECRET")
    assert poipoi_hmac.provider == "app"
    assert poipoi_hmac.rotation_class == "propagate_only"

    poipoi_security_flag = classify_secret("poipoi-https", "BAAS_FACTORY_REQUIRE_HTTPS")
    assert poipoi_security_flag.provider == "app"
    assert poipoi_security_flag.rotation_class == "propagate_only"

    unknown = classify_secret("mystery", "MYSTERY_INTERNAL_TOKEN")
    assert unknown.rotation_class == "review_required"


def test_classify_config_and_summary():
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(name="openai", account="openai", env_name="OPENAI_API_KEY"))
    config.add_secret(SecretEntry(name="github", account="github", env_name="GITHUB_TOKEN"))
    config.add_secret(SecretEntry(name="owner", account="line-owner", env_name="LINE_OWNER_USER_ID"))
    config.add_secret(SecretEntry(name="hmac", account="hmac", env_name="HMAC_SECRET"))

    items = classify_config(config)
    summary = summarize_classifications(items)

    assert summary["full_auto"] == 1
    assert summary["propagate_only"] == 1
    assert summary["inventory_only"] == 1
    assert summary["manual_cutover"] == 1


def test_cmd_sync_classify_text_and_json(tmp_path: Path, capsys):
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(name="openai", account="openai", env_name="OPENAI_API_KEY"))
    config.add_secret(SecretEntry(name="line-owner", account="line-owner", env_name="LINE_OWNER_USER_ID"))
    config_path = tmp_path / "sync.json"
    config.save(config_path)

    cmd_sync_classify(["--config", str(config_path)])
    text_out = capsys.readouterr().out
    assert "BANTO SYNC CLASSIFY" in text_out
    assert "OPENAI_API_KEY" in text_out
    assert "LINE_OWNER_USER_ID" in text_out

    cmd_sync_classify(["--config", str(config_path), "--json"])
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert payload["count"] == 2
    assert payload["summary"]["full_auto"] == 1
    assert payload["summary"]["inventory_only"] == 1

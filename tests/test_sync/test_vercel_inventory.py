# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for read-only Vercel env inventory."""
from __future__ import annotations

from unittest.mock import patch

from banto.sync.config import SecretEntry, SyncConfig
from banto.sync.vercel_inventory import (
    build_vercel_inventory_report,
    parse_vercel_env_json_output,
    report_to_json,
)


def test_parse_vercel_env_json_output_tolerates_progress_text():
    parsed = parse_vercel_env_json_output(
        'Retrieving project...\n{"envs":[{"key":"OPENAI_API_KEY","target":["production"]}]}\n'
    )

    assert parsed == [{"key": "OPENAI_API_KEY", "target": ["production"]}]


@patch("banto.sync.vercel_inventory.collect_vercel_project_envs")
def test_build_vercel_inventory_classifies_without_secret_values(mock_collect):
    mock_collect.return_value = [
        {
            "key": "OPENAI_API_KEY",
            "type": "encrypted",
            "target": ["production"],
            "createdAt": 1760000000000,
            "updatedAt": 1760000000000,
        },
        {
            "key": "XAI_API_KEY",
            "type": "sensitive",
            "target": ["production"],
            "createdAt": 1760000000000,
            "updatedAt": 1760000000000,
        },
        {
            "key": "NEXT_PUBLIC_APP_URL",
            "type": "encrypted",
            "target": ["production"],
            "createdAt": 1760000000000,
            "updatedAt": 1760000000000,
        },
    ]
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
    ))

    report = build_vercel_inventory_report(
        config,
        ["app"],
        exclude_envs=["XAI_API_KEY"],
    )
    payload = report_to_json(report)

    assert payload["count"] == 3
    assert payload["counts_by_lane"]["rotate_now"] == 1
    assert payload["counts_by_lane"]["excluded"] == 1
    assert payload["counts_by_lane"]["monitor_only"] == 1
    assert payload["sensitive_upgrade_count"] == 1
    openai = next(item for item in payload["items"] if item["env_name"] == "OPENAI_API_KEY")
    assert openai["managed_by_sync"] is True
    assert openai["needs_sensitive_upgrade"] is True

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for incident-response prioritization."""
from __future__ import annotations

from pathlib import Path

from banto.sync.cli import cmd_sync_incident_report
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.incident_report import build_incident_report


def _build_config(tmp_path: Path) -> tuple[SyncConfig, Path]:
    config = SyncConfig(keychain_service="test-sync")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        targets=[Target(platform="vercel", project="app1")],
    ))
    config.add_secret(SecretEntry(
        name="github",
        account="github",
        env_name="GITHUB_TOKEN",
        targets=[Target(platform="vercel", project="app2")],
    ))
    config.add_secret(SecretEntry(
        name="xai",
        account="xai",
        env_name="XAI_API_KEY",
        targets=[Target(platform="vercel", project="app2")],
    ))
    config.add_secret(SecretEntry(
        name="gemini",
        account="shared-google",
        env_name="GEMINI_API_KEY",
        targets=[Target(platform="vercel", project="app3")],
    ))
    config.add_secret(SecretEntry(
        name="google",
        account="shared-google",
        env_name="GOOGLE_API_KEY",
        targets=[Target(platform="vercel", project="app3")],
    ))
    config.add_secret(SecretEntry(
        name="hmac",
        account="hmac",
        env_name="HMAC_SECRET",
        targets=[Target(platform="vercel", project="app4")],
    ))
    config.add_secret(SecretEntry(
        name="line-owner",
        account="line-owner",
        env_name="LINE_OWNER_USER_ID",
    ))
    path = tmp_path / "sync.json"
    config.save(path)
    return config, path


def test_build_incident_report(tmp_path: Path):
    config, _ = _build_config(tmp_path)
    report = build_incident_report(config)

    assert report.counts_by_lane()["rotate_now"] == 4
    assert report.counts_by_lane()["approval_gated"] == 1
    assert report.counts_by_lane()["manual_cutover"] == 1
    assert report.counts_by_lane()["monitor_only"] == 1

    openai = next(plan for plan in report.plans if plan.secret_name == "openai")
    assert openai.incident_lane == "rotate_now"
    assert "openai-service-account" in openai.recommended_command

    google = next(plan for plan in report.plans if plan.secret_name == "google")
    assert google.shared_account_secret_names == ("gemini",)
    assert "google-api-key" in google.recommended_command

    xai = next(plan for plan in report.plans if plan.secret_name == "xai")
    assert xai.incident_lane == "rotate_now"
    assert "xai-api-key" in xai.recommended_command

    hmac = next(plan for plan in report.plans if plan.secret_name == "hmac")
    assert hmac.incident_lane == "manual_cutover"
    assert "manual-cutover-rotation-runbook" in hmac.recommended_command


def test_cmd_sync_incident_report_human_output(tmp_path: Path, capsys):
    _, config_path = _build_config(tmp_path)

    cmd_sync_incident_report(["--config", str(config_path)])
    out = capsys.readouterr().out

    assert "BANTO SYNC INCIDENT REPORT" in out
    assert "Rotate Now" in out
    assert "OPENAI_API_KEY" in out
    assert "shared:" in out


def test_cmd_sync_incident_report_json_output(tmp_path: Path, capsys):
    _, config_path = _build_config(tmp_path)

    cmd_sync_incident_report(["--json", "--config", str(config_path)])
    out = capsys.readouterr().out

    assert "\"counts_by_lane\"" in out
    assert "\"incident_lane\": \"manual_cutover\"" in out

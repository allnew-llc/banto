# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.sync.setup — auto-detect and configure sync targets."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from banto.sync.config import SecretEntry, SyncConfig, Target
from banto.sync.setup import (
    ENV_TO_KEYCHAIN,
    _find_keychain_match,
    _is_excluded,
    _list_vercel_env_vars,
    run_setup,
)


# ── _is_excluded ──────────────────────────────────────────────────


class TestIsExcluded:
    def test_exact_match(self):
        assert _is_excluded("gh:github.com") is True

    def test_pattern_match(self):
        assert _is_excluded("some-oauth-token") is True
        assert _is_excluded("my-refresh-token") is True
        assert _is_excluded("claude-mcp-freee-access-token") is True

    def test_safe_service(self):
        assert _is_excluded("claude-mcp-openai") is False
        assert _is_excluded("banto-github") is False


# ── ENV_TO_KEYCHAIN consistency ───────────────────────────────────


class TestEnvToKeychainConsistency:
    """Verify gh:github.com is not in ENV_TO_KEYCHAIN (S3 fix)."""

    def test_no_excluded_services_in_catalog(self):
        from banto.sync.validate import EXCLUDED_SERVICES

        for env_var, candidates in ENV_TO_KEYCHAIN:
            for svc in candidates:
                assert svc not in EXCLUDED_SERVICES, (
                    f"ENV_TO_KEYCHAIN[{env_var}] contains excluded service {svc!r}"
                )

    def test_gh_github_not_in_github_token(self):
        for env_var, candidates in ENV_TO_KEYCHAIN:
            if env_var == "GITHUB_TOKEN":
                assert "gh:github.com" not in candidates


# ── _find_keychain_match ──────────────────────────────────────────


class TestFindKeychainMatch:
    @patch("banto.sync.setup._keychain_exists")
    def test_known_mapping_match(self, mock_exists):
        mock_exists.side_effect = lambda s: s == "claude-mcp-openai"
        assert _find_keychain_match("OPENAI_API_KEY") == "claude-mcp-openai"

    @patch("banto.sync.setup._keychain_exists")
    def test_known_mapping_no_match(self, mock_exists):
        mock_exists.return_value = False
        assert _find_keychain_match("OPENAI_API_KEY") is None

    @patch("banto.sync.setup._keychain_exists")
    def test_excluded_service_skipped(self, mock_exists):
        """Even if gh:github.com exists in Keychain, it should not match."""
        mock_exists.side_effect = lambda s: s == "gh:github.com"
        assert _find_keychain_match("GITHUB_TOKEN") is None

    @patch("banto.sync.setup._keychain_exists")
    def test_fallback_heuristic(self, mock_exists):
        mock_exists.side_effect = lambda s: s == "banto-my-custom-key"
        assert _find_keychain_match("MY_CUSTOM_KEY") == "banto-my-custom-key"

    @patch("banto.sync.setup._keychain_exists")
    def test_fallback_excludes_oauth_patterns(self, mock_exists):
        """Heuristic fallback should skip services matching excluded patterns."""
        mock_exists.return_value = True
        # "my-oauth-key" contains "oauth" pattern → excluded
        assert _find_keychain_match("MY_OAUTH_KEY") is None


# ── _list_vercel_env_vars ─────────────────────────────────────────


class TestListVercelEnvVars:
    @patch("banto.sync.setup.subprocess.run")
    def test_passes_project_flag(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OPENAI_API_KEY\nDATABASE_URL\n"
        mock_run.return_value = mock_result

        result = _list_vercel_env_vars("my-project")

        # Verify --project flag is passed
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "--project" in cmd
        assert "my-project" in cmd
        assert result == ["OPENAI_API_KEY", "DATABASE_URL"]

    @patch("banto.sync.setup.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.side_effect = OSError("vercel not found")
        assert _list_vercel_env_vars("any-project") == []


# ── run_setup ─────────────────────────────────────────────────────


class TestRunSetup:
    @patch("banto.sync.setup._find_keychain_match")
    @patch("banto.sync.setup.PLATFORM_SCANNERS", {"vercel": MagicMock(return_value=[])})
    def test_fail_closed_when_discovery_empty(self, mock_match):
        """When scanner returns empty, run_setup should return discovery_empty."""
        cfg = SyncConfig(keychain_service="test")
        matches = run_setup(
            platform="vercel", project="my-proj", config=cfg, dry_run=True,
        )
        assert len(matches) == 1
        assert matches[0].status == "discovery_empty"
        mock_match.assert_not_called()

    @patch("banto.sync.setup._find_keychain_match")
    @patch("banto.sync.setup.PLATFORM_SCANNERS", {"vercel": MagicMock(return_value=[])})
    def test_guess_mode_uses_catalog(self, mock_match):
        """With guess=True, empty discovery falls back to ENV_TO_KEYCHAIN."""
        mock_match.return_value = None
        cfg = SyncConfig(keychain_service="test")
        matches = run_setup(
            platform="vercel", project="my-proj", config=cfg,
            dry_run=True, guess=True,
        )
        # Should have tried all ENV_TO_KEYCHAIN entries, not discovery_empty
        assert all(m.status != "discovery_empty" for m in matches)
        assert len(matches) == len(ENV_TO_KEYCHAIN)

    @patch("banto.sync.setup._find_keychain_match")
    @patch("banto.sync.setup.PLATFORM_SCANNERS", {
        "vercel": MagicMock(return_value=["OPENAI_API_KEY", "DB_URL"]),
    })
    def test_scanner_results_used_directly(self, mock_match):
        """When scanner returns results, those are used (not catalog)."""
        mock_match.side_effect = lambda e: "openai" if e == "OPENAI_API_KEY" else None
        cfg = SyncConfig(keychain_service="test")
        matches = run_setup(
            platform="vercel", project="my-proj", config=cfg, dry_run=True,
        )
        assert len(matches) == 2
        statuses = {m.env_var: m.status for m in matches}
        assert statuses["OPENAI_API_KEY"] == "matched"
        assert statuses["DB_URL"] == "missing"

    @patch("banto.sync.setup._find_keychain_match")
    @patch("banto.sync.setup.PLATFORM_SCANNERS", {
        "vercel": MagicMock(return_value=["OPENAI_API_KEY"]),
    })
    def test_already_configured_skipped(self, mock_match):
        """Env vars already in sync.json get 'already_configured' status."""
        cfg = SyncConfig(keychain_service="test")
        cfg.add_secret(SecretEntry(
            name="openai",
            account="openai",
            env_name="OPENAI_API_KEY",
            targets=[Target(platform="vercel", project="my-proj")],
        ))
        matches = run_setup(
            platform="vercel", project="my-proj", config=cfg, dry_run=True,
        )
        assert len(matches) == 1
        assert matches[0].status == "already_configured"
        mock_match.assert_not_called()

    @patch("banto.sync.setup._find_keychain_match")
    def test_unknown_platform_fails_closed(self, mock_match):
        """Unknown platform (no scanner) should also fail closed."""
        cfg = SyncConfig(keychain_service="test")
        matches = run_setup(
            platform="unknown-platform", project="proj", config=cfg, dry_run=True,
        )
        assert len(matches) == 1
        assert matches[0].status == "discovery_empty"

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for sync state (fingerprint drift detection) and validate module."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from banto.sync.sync_state import SyncState, fingerprint, PushRecord


class TestFingerprint:
    def test_deterministic(self):
        assert fingerprint("hello") == fingerprint("hello")

    def test_different_values(self):
        assert fingerprint("a") != fingerprint("b")

    def test_length(self):
        assert len(fingerprint("anything")) == 12


class TestSyncState:
    def test_record_and_retrieve(self, tmp_path: Path):
        state = SyncState(path=tmp_path / "state.json")
        rec = state.record_push("openai", "sk-secret", ["vercel:app"])
        assert rec.fingerprint == fingerprint("sk-secret")
        assert "vercel:app" in rec.targets

        retrieved = state.get_push_record("openai")
        assert retrieved is not None
        assert retrieved.fingerprint == rec.fingerprint

    def test_persistence(self, tmp_path: Path):
        path = tmp_path / "state.json"
        s1 = SyncState(path=path)
        s1.record_push("x", "val", ["t1"])

        s2 = SyncState(path=path)
        assert s2.get_push_record("x") is not None

    def test_drift_in_sync(self, tmp_path: Path):
        state = SyncState(path=tmp_path / "state.json")
        state.record_push("key", "value-1", ["t"])
        assert state.check_drift("key", "value-1") == "in_sync"

    def test_drift_local_change(self, tmp_path: Path):
        state = SyncState(path=tmp_path / "state.json")
        state.record_push("key", "old-value", ["t"])
        assert state.check_drift("key", "new-value") == "drift_local"

    def test_drift_never_pushed(self, tmp_path: Path):
        state = SyncState(path=tmp_path / "state.json")
        assert state.check_drift("unknown", "val") == "never_pushed"

    def test_remove(self, tmp_path: Path):
        state = SyncState(path=tmp_path / "state.json")
        state.record_push("x", "v", ["t"])
        state.remove("x")
        assert state.get_push_record("x") is None

    def test_no_value_in_file(self, tmp_path: Path):
        path = tmp_path / "state.json"
        state = SyncState(path=path)
        state.record_push("secret", "super-secret-value", ["t"])
        content = path.read_text()
        assert "super-secret-value" not in content

    def test_file_permissions(self, tmp_path: Path):
        import stat
        path = tmp_path / "state.json"
        SyncState(path=path).record_push("x", "v", ["t"])
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


class TestValidateModule:
    @patch("banto.sync.validate._http_get")
    def test_openai_valid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (200, '{"data": []}')
        result = validate_key("openai", "sk-test")
        assert result.valid is True

    @patch("banto.sync.validate._http_get")
    def test_openai_invalid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (401, '{"error": "invalid"}')
        result = validate_key("openai", "sk-bad")
        assert result.valid is False
        assert result.status_code == 401

    @patch("banto.sync.validate._http_get")
    def test_anthropic_valid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (200, '{}')
        result = validate_key("anthropic", "sk-ant-test")
        assert result.valid is True

    @patch("banto.sync.validate._http_get")
    def test_github_valid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (200, '{"login": "user"}')
        result = validate_key("github", "ghp_test")
        assert result.valid is True

    @patch("banto.sync.validate._http_get")
    def test_cloudflare_valid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (200, '{"success": true}')
        result = validate_key("cloudflare", "cf-token")
        assert result.valid is True

    @patch("banto.sync.validate._http_get")
    def test_pattern_match(self, mock_get):
        """Keychain service name like 'claude-mcp-openai' should match openai."""
        from banto.sync.validate import validate_key
        mock_get.return_value = (200, '{}')
        result = validate_key("claude-mcp-openai", "sk-test")
        assert result.valid is True
        assert result.provider == "openai"

    def test_unknown_provider(self):
        from banto.sync.validate import validate_key
        result = validate_key("unknown-service", "key")
        assert result.valid is True  # Assumed valid
        assert "No validator" in result.message

    @patch("banto.sync.validate._http_get")
    def test_rate_limited_still_valid(self, mock_get):
        from banto.sync.validate import validate_key
        mock_get.return_value = (429, '{}')
        result = validate_key("openai", "sk-test")
        assert result.valid is True  # 429 = key accepted, just rate limited

    def test_list_supported(self):
        from banto.sync.validate import list_supported_providers
        providers = list_supported_providers()
        assert "openai" in providers
        assert "anthropic" in providers
        assert "github" in providers

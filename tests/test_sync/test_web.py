# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.sync.web — local web UI."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import pytest
from banto.sync.config import SecretEntry, Target, SyncConfig
from banto.sync.web import SyncUIHandler, _build_config_json, _build_status_json, serve


@pytest.fixture
def sample_config() -> SyncConfig:
    config = SyncConfig(keychain_service="test-vault")
    config.add_secret(SecretEntry(
        name="openai",
        account="openai",
        env_name="OPENAI_API_KEY",
        description="OpenAI API Key",
        targets=[Target(platform="cloudflare-pages", project="my-project")],
    ))
    return config


class TestBuildStatusJson:
    def test_returns_list(self, sample_config: SyncConfig):
        with patch("banto.sync.web.check_status") as mock:
            from banto.sync.sync import StatusEntry
            mock.return_value = [
                StatusEntry(
                    secret_name="openai",
                    env_name="OPENAI_API_KEY",
                    keychain_exists=True,
                    target_status={"cloudflare-pages:my-project": True},
                ),
            ]
            result = _build_status_json(sample_config)
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["name"] == "openai"
            assert result[0]["keychain"] is True

    def test_no_secret_values_in_output(self, sample_config: SyncConfig):
        with patch("banto.sync.web.check_status") as mock:
            from banto.sync.sync import StatusEntry
            mock.return_value = [
                StatusEntry(
                    secret_name="openai",
                    env_name="OPENAI_API_KEY",
                    keychain_exists=True,
                    target_status={},
                ),
            ]
            result = _build_status_json(sample_config)
            # Ensure no "value" or secret-like keys in output
            for entry in result:
                assert "value" not in entry
                assert "secret" not in entry


class TestBuildConfigJson:
    def test_returns_metadata(self, sample_config: SyncConfig):
        result = _build_config_json(sample_config)
        assert result["keychain_service"] == "test-vault"
        assert len(result["secrets"]) == 1
        assert result["secrets"][0]["name"] == "openai"
        assert result["secrets"][0]["env_name"] == "OPENAI_API_KEY"

    def test_no_secret_values(self, sample_config: SyncConfig):
        result = _build_config_json(sample_config)
        for s in result["secrets"]:
            assert "value" not in s


class TestServeIntegration:
    def test_html_page_served(self, sample_config: SyncConfig):
        """Test that the UI serves HTML on /."""
        port = 18390  # Use high port to avoid conflicts

        with patch("banto.sync.web.webbrowser.open"):
            server_thread = threading.Thread(
                target=serve, args=(sample_config, port), daemon=True,
            )
            server_thread.start()
            time.sleep(0.3)  # Wait for server to start

            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                resp = conn.getresponse()
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                assert "banto sync" in body
                assert "<script>" in body
                conn.close()
            finally:
                # No clean shutdown API, daemon thread will die with test
                pass

    def test_api_config_endpoint(self, sample_config: SyncConfig):
        """Test that /api/config returns JSON metadata."""
        port = 18391

        with patch("banto.sync.web.webbrowser.open"):
            server_thread = threading.Thread(
                target=serve, args=(sample_config, port), daemon=True,
            )
            server_thread.start()
            time.sleep(0.3)

            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/config")
                resp = conn.getresponse()
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["keychain_service"] == "test-vault"
                assert len(data["secrets"]) == 1
                conn.close()
            finally:
                pass

    def test_404_for_unknown_path(self, sample_config: SyncConfig):
        """Test that unknown paths return 404."""
        port = 18392

        with patch("banto.sync.web.webbrowser.open"):
            server_thread = threading.Thread(
                target=serve, args=(sample_config, port), daemon=True,
            )
            server_thread.start()
            time.sleep(0.3)

            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/nonexistent")
                resp = conn.getresponse()
                assert resp.status == 404
                conn.close()
            finally:
                pass

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto sync version history — Keychain-native rollback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from banto.sync.history import (
    HistoryStore,
    _fingerprint,
    _history_service,
    _version_account,
)


class TestFingerprint:
    def test_deterministic(self):
        assert _fingerprint("hello") == _fingerprint("hello")

    def test_different_values(self):
        assert _fingerprint("a") != _fingerprint("b")

    def test_length(self):
        assert len(_fingerprint("anything")) == 12


class TestHelpers:
    def test_history_service(self):
        assert _history_service("banto-sync") == "banto-sync-history"

    def test_version_account(self):
        assert _version_account("openai", 3) == "openai--v3"


class TestHistoryStore:
    @patch("banto.sync.history.KeychainStore")
    def test_record_and_list(self, mock_kc_cls, tmp_path: Path):
        mock_kc_cls.return_value = mock_kc_cls
        store = HistoryStore(path=tmp_path / "history.json")
        v1 = store.record("openai", "value-1", "vault-key")
        assert v1.version == 1
        assert v1.fingerprint == _fingerprint("value-1")

        v2 = store.record("openai", "value-2", "vault-key")
        assert v2.version == 2

        versions = store.list_versions("openai")
        assert len(versions) == 2

    @patch("banto.sync.history.KeychainStore")
    def test_record_stores_in_keychain(self, mock_kc_cls, tmp_path: Path):
        mock_kc = mock_kc_cls.return_value
        store = HistoryStore(path=tmp_path / "history.json")
        store.record("openai", "sk-secret-123", "banto-sync")

        mock_kc.store.assert_called_once_with(
            "openai--v1", "sk-secret-123",
        )

    @patch("banto.sync.history.KeychainStore")
    def test_get_version_value_from_keychain(self, mock_kc_cls, tmp_path: Path):
        mock_kc = mock_kc_cls.return_value
        mock_kc.get.return_value = "original-value"

        store = HistoryStore(path=tmp_path / "history.json")
        store.record("test", "original-value", "key")
        store.record("test", "rotated-value", "key")

        val = store.get_version_value("test", 1, "key")
        assert val == "original-value"
        mock_kc.get.assert_called_with("test--v1")

    @patch("banto.sync.history.KeychainStore")
    def test_get_version_nonexistent_secret(self, mock_kc_cls, tmp_path: Path):
        mock_kc_cls.return_value = mock_kc_cls
        store = HistoryStore(path=tmp_path / "history.json")
        assert store.get_version_value("nope", 1, "key") is None

    @patch("banto.sync.history.KeychainStore")
    def test_get_version_nonexistent_version(self, mock_kc_cls, tmp_path: Path):
        mock_kc = mock_kc_cls.return_value
        store = HistoryStore(path=tmp_path / "history.json")
        store.record("test", "val", "key")
        assert store.get_version_value("test", 99, "key") is None
        mock_kc.get.assert_not_called()

    @patch("banto.sync.history.KeychainStore")
    def test_get_version_keychain_error(self, mock_kc_cls, tmp_path: Path):
        from banto.keychain import KeyNotFoundError
        mock_kc = mock_kc_cls.return_value
        mock_kc.get.return_value = None  # KeychainStore.get returns None on not found

        store = HistoryStore(path=tmp_path / "history.json")
        store.record("test", "val", "key")
        assert store.get_version_value("test", 1, "key") is None

    @patch("banto.sync.history.KeychainStore")
    def test_persistence(self, mock_kc_cls, tmp_path: Path):
        mock_kc_cls.return_value = mock_kc_cls
        path = tmp_path / "history.json"
        store1 = HistoryStore(path=path)
        store1.record("x", "val", "key")

        store2 = HistoryStore(path=path)
        assert len(store2.list_versions("x")) == 1

    @patch("banto.sync.history.KeychainStore")
    def test_no_encrypted_value_in_json(self, mock_kc_cls, tmp_path: Path):
        """JSON file must NEVER contain secret values or encrypted_value fields."""
        mock_kc_cls.return_value = mock_kc_cls
        path = tmp_path / "history.json"
        store = HistoryStore(path=path)
        store.record("test", "super-secret-value", "key")

        content = path.read_text()
        assert "super-secret-value" not in content
        assert "encrypted_value" not in content

    @patch("banto.sync.history.KeychainStore")
    def test_max_versions_trims_keychain(self, mock_kc_cls, tmp_path: Path):
        mock_kc = mock_kc_cls.return_value
        store = HistoryStore(path=tmp_path / "history.json")
        for i in range(55):
            store.record("many", f"val-{i}", "svc")
        versions = store.list_versions("many")
        assert len(versions) == 50

        # Old versions should have been deleted from Keychain
        deleted_accounts = [
            call.args[0] for call in mock_kc.delete.call_args_list
        ]
        assert "many--v1" in deleted_accounts
        assert "many--v5" in deleted_accounts

    @patch("banto.sync.history.KeychainStore")
    def test_remove_cleans_keychain(self, mock_kc_cls, tmp_path: Path):
        mock_kc = mock_kc_cls.return_value
        store = HistoryStore(path=tmp_path / "history.json")
        store.record("x", "val1", "svc")
        store.record("x", "val2", "svc")
        store.remove("x", sync_service="svc")
        assert store.list_versions("x") == []

        deleted_accounts = [
            call.args[0] for call in mock_kc.delete.call_args_list
        ]
        assert "x--v1" in deleted_accounts
        assert "x--v2" in deleted_accounts

    @patch("banto.sync.history.KeychainStore")
    def test_remove_without_service(self, mock_kc_cls, tmp_path: Path):
        """remove() without sync_service skips Keychain cleanup."""
        mock_kc = mock_kc_cls.return_value
        store = HistoryStore(path=tmp_path / "history.json")
        store.record("x", "val", "svc")
        store.remove("x")
        assert store.list_versions("x") == []
        # No delete calls for cleanup (only store from record)
        mock_kc.delete.assert_not_called()

    @patch("banto.sync.history.KeychainStore")
    def test_get_history_none(self, mock_kc_cls, tmp_path: Path):
        mock_kc_cls.return_value = mock_kc_cls
        store = HistoryStore(path=tmp_path / "history.json")
        assert store.get_history("nope") is None

    @patch("banto.sync.history.KeychainStore")
    def test_record_keychain_failure_does_not_save_metadata(self, mock_kc_cls, tmp_path: Path):
        """If Keychain store fails, no metadata is recorded (fail-closed)."""
        from banto.keychain import KeyNotFoundError
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.side_effect = KeyNotFoundError("test")

        store = HistoryStore(path=tmp_path / "history.json")
        v = store.record("test", "value", "svc")
        assert v is None
        assert len(store.list_versions("test")) == 0

    @patch("banto.sync.history.KeychainStore")
    def test_record_keychain_returns_false_does_not_save(self, mock_kc_cls, tmp_path: Path):
        """If Keychain store returns False, no metadata is recorded (fail-closed)."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = False

        store = HistoryStore(path=tmp_path / "history.json")
        v = store.record("test", "value", "svc")
        assert v is None
        assert len(store.list_versions("test")) == 0

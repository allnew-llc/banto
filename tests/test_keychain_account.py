# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Keychain account resolution tests for non-interactive agent shells."""
from __future__ import annotations

from unittest.mock import patch

from banto.keychain import KeychainStore, default_keychain_account


def test_default_keychain_account_prefers_user_over_root_getlogin(monkeypatch):
    monkeypatch.setenv("USER", "masa")
    monkeypatch.setenv("LOGNAME", "root")

    with patch("banto.keychain.getpass.getuser", return_value="root"):
        with patch("banto.keychain.os.getlogin", return_value="root"):
            assert default_keychain_account() == "masa"


def test_keychain_store_uses_default_account(monkeypatch):
    monkeypatch.setenv("USER", "masa")

    assert KeychainStore(service_prefix="banto-sync").account == "masa"


def test_keychain_store_accepts_explicit_account(monkeypatch):
    monkeypatch.setenv("USER", "masa")

    assert KeychainStore(service_prefix="allnew-x", account="allnew_llc").account == "allnew_llc"


def test_keychain_store_rejects_invalid_explicit_account():
    for account in ("", "bad\x00account"):
        try:
            KeychainStore(service_prefix="allnew-x", account=account)
        except ValueError:
            continue
        raise AssertionError(f"Expected invalid account to fail: {account!r}")

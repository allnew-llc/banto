# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.lease — dynamic secrets with TTL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from banto.lease import LeaseInfo, LeaseManager, LeaseState


class TestLeaseState:
    def test_save_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "state.json"
        state = LeaseState(leases={
            "lease-abc": {"name": "test", "status": "active"},
        })
        state.save(path)

        loaded = LeaseState.load(path)
        assert "lease-abc" in loaded.leases
        assert loaded.leases["lease-abc"]["status"] == "active"

    def test_load_nonexistent(self, tmp_path: Path):
        state = LeaseState.load(tmp_path / "missing.json")
        assert state.leases == {}

    def test_load_corrupt(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        state = LeaseState.load(path)
        assert state.leases == {}

    def test_file_permissions(self, tmp_path: Path):
        import stat
        path = tmp_path / "state.json"
        LeaseState().save(path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


class TestLeaseManager:
    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_acquire(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="temp-cred-123\n")
        mock_kc_cls.return_value.store.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info = mgr.acquire(name="db", ttl_seconds=600, cmd="echo cred")

        assert info.value == "temp-cred-123"
        assert info.name == "db"
        assert info.ttl_seconds == 600
        assert info.lease_id.startswith("lease-")
        mock_kc_cls.return_value.store.assert_called_once()

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_acquire_command_failure(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 1, stderr="error")

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        with pytest.raises(RuntimeError, match="Command failed"):
            mgr.acquire(name="db", cmd="failing-cmd")

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_get_value_active(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_kc.get.return_value = "cred"

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info = mgr.acquire(name="db", ttl_seconds=3600, cmd="echo cred")

        val = mgr.get_value(info.lease_id)
        assert val == "cred"

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_get_value_expired(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_kc.delete.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info = mgr.acquire(name="db", ttl_seconds=3600, cmd="echo cred")

        # Manually expire the lease
        meta = mgr._state.leases[info.lease_id]
        meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        val = mgr.get_value(info.lease_id)
        assert val is None  # Expired → auto-revoked

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_revoke(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_kc.get.return_value = "cred"
        mock_kc.delete.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info = mgr.acquire(name="db", ttl_seconds=3600, cmd="echo cred")

        result = mgr.revoke(info.lease_id)
        assert result is True
        assert mgr._state.leases[info.lease_id]["status"] == "revoked"
        mock_kc.delete.assert_called()

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_revoke_with_cmd(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_kc.get.return_value = "key-abc"
        mock_kc.delete.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info = mgr.acquire(
            name="db", ttl_seconds=3600, cmd="echo cred",
            revoke_cmd="delete-key $BANTO_LEASE_VALUE",
        )
        mgr.revoke(info.lease_id)

        # Second subprocess.run call should be the revoke command
        revoke_call = mock_run.call_args_list[-1]
        assert "delete-key" in str(revoke_call)
        # Value must be passed via env, NOT expanded into argv
        revoke_env = revoke_call.kwargs.get("env", {})
        assert revoke_env.get("BANTO_LEASE_VALUE") == "key-abc"

    def test_revoke_nonexistent(self, tmp_path: Path):
        with patch("banto.lease.KeychainStore"):
            mgr = LeaseManager(state_path=tmp_path / "state.json")
            assert mgr.revoke("nonexistent") is False

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_list_leases(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc_cls.return_value.store.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        mgr.acquire(name="db1", ttl_seconds=3600, cmd="echo cred")
        mgr.acquire(name="db2", ttl_seconds=7200, cmd="echo cred")

        active = mgr.list_leases()
        assert len(active) == 2
        names = {l["name"] for l in active}
        assert names == {"db1", "db2"}

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_cleanup(self, mock_run, mock_kc_cls, tmp_path: Path):
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="cred\n")
        mock_kc = mock_kc_cls.return_value
        mock_kc.store.return_value = True
        mock_kc.delete.return_value = True

        mgr = LeaseManager(state_path=tmp_path / "state.json")
        info1 = mgr.acquire(name="expired", ttl_seconds=1, cmd="echo a")
        info2 = mgr.acquire(name="active", ttl_seconds=99999, cmd="echo b")

        # Expire info1
        mgr._state.leases[info1.lease_id]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()

        count = mgr.cleanup()
        assert count == 1
        assert mgr._state.leases[info1.lease_id]["status"] == "revoked"
        assert mgr._state.leases[info2.lease_id]["status"] == "active"

    @patch("banto.lease.KeychainStore")
    @patch("banto.lease.subprocess.run")
    def test_no_value_in_state_file(self, mock_run, mock_kc_cls, tmp_path: Path):
        """State file must NEVER contain credential values."""
        import subprocess as sp
        mock_run.return_value = sp.CompletedProcess([], 0, stdout="super-secret\n")
        mock_kc_cls.return_value.store.return_value = True

        state_path = tmp_path / "state.json"
        mgr = LeaseManager(state_path=state_path)
        mgr.acquire(name="db", cmd="echo cred")

        content = state_path.read_text()
        assert "super-secret" not in content
        assert "value" not in content  # no "value" key


class TestVaultNoBudget:
    """Test SecureVault with budget=False."""

    def test_get_key_by_provider(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        mock_backend.get.return_value = "sk-test-key"

        vault = SecureVault(budget=False, backend=mock_backend)
        key = vault.get_key(provider="openai")
        assert key == "sk-test-key"

    def test_get_key_no_budget_check(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        mock_backend.get.return_value = "sk-test"

        vault = SecureVault(budget=False, backend=mock_backend)
        # Should work without model/tokens — just provider
        key = vault.get_key(provider="openai")
        assert key == "sk-test"
        assert vault.budget_enabled is False

    def test_get_key_missing_raises(self):
        from banto.vault import SecureVault
        from banto.keychain import KeyNotFoundError
        mock_backend = MagicMock()
        mock_backend.get.return_value = None

        vault = SecureVault(budget=False, backend=mock_backend)
        with pytest.raises(KeyNotFoundError):
            vault.get_key(provider="missing")

    def test_record_usage_noop(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        vault = SecureVault(budget=False, backend=mock_backend)
        result = vault.record_usage(model="gpt-4o")
        assert result == {"budget_enabled": False}

    def test_budget_status_disabled(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        vault = SecureVault(budget=False, backend=mock_backend)
        status = vault.get_budget_status()
        assert status["budget_enabled"] is False

    def test_estimate_cost_zero(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        vault = SecureVault(budget=False, backend=mock_backend)
        cost = vault.estimate_cost(model="gpt-4o")
        assert cost == 0.0

    def test_no_provider_no_model_raises(self):
        from banto.vault import SecureVault
        mock_backend = MagicMock()
        vault = SecureVault(budget=False, backend=mock_backend)
        with pytest.raises(ValueError):
            vault.get_key()

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto.lease — dynamic secrets with TTL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from banto.lease import LeaseManager, LeaseState


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
        mgr._state.save(mgr._state_path)  # cleanup() re-reads state under a lock

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


# ── 5.3.0: revocation correctness, atomic state, TTL bounds ──────


class _FakeKeychain:
    def __init__(self, *args, **kwargs):
        self.items: dict[str, str] = {}

    def store(self, name, value):
        self.items[name] = value
        return True

    def get(self, name):
        return self.items.get(name)

    def delete(self, name):
        return self.items.pop(name, None) is not None


@pytest.fixture
def real_mgr(tmp_path, monkeypatch):
    monkeypatch.setattr("banto.lease.KeychainStore", _FakeKeychain)
    return LeaseManager(state_path=tmp_path / "state.json")


def test_revoke_cmd_expands_lease_value_via_shell(real_mgr, tmp_path):
    out = tmp_path / "revoked.txt"
    info = real_mgr.acquire(
        name="k", ttl_seconds=60, cmd="echo FAKE_LEASE_VALUE",
        revoke_cmd=f'sh -c \'true\' && printf %s "$BANTO_LEASE_VALUE" > {out}',
    )
    assert real_mgr.revoke(info.lease_id) is True
    assert out.read_text() == "FAKE_LEASE_VALUE"
    assert real_mgr._state.leases[info.lease_id]["status"] == "revoked"
    assert real_mgr._kc.get(info.lease_id) is None


def test_revoke_argv_value_never_in_banto_argv():
    from banto.lease import revoke_argv
    argv = revoke_argv("delete-key --id $BANTO_LEASE_VALUE --lease {lease_id}", "lease-1")
    assert argv == ["/bin/sh", "-c", "delete-key --id $BANTO_LEASE_VALUE --lease lease-1"]
    assert revoke_argv("delete-key ${BANTO_LEASE_VALUE}", "x")[0] == "/bin/sh"
    assert revoke_argv("delete-key --lease {lease_id}", "lease-2") == ["delete-key", "--lease", "lease-2"]


def test_failed_revoke_is_kept_and_retried(real_mgr, tmp_path):
    flag = tmp_path / "allow"
    info = real_mgr.acquire(
        name="k", ttl_seconds=60, cmd="echo FAKE",
        revoke_cmd=f'test -e {flag} && echo "$BANTO_LEASE_VALUE" >/dev/null',
    )
    assert real_mgr.revoke(info.lease_id) is False
    meta = real_mgr._state.leases[info.lease_id]
    assert meta["status"] == "revoke_failed"
    assert meta["revoke_error"].startswith("exit ")
    assert meta["revoke_attempts"] == 1
    assert real_mgr._kc.get(info.lease_id) == "FAKE"  # credential kept for retry
    assert real_mgr.get_value(info.lease_id) is None
    assert [p["lease_id"] for p in real_mgr.pending_revocation()] == [info.lease_id]

    assert real_mgr.cleanup() == 0  # still failing
    assert real_mgr._state.leases[info.lease_id]["revoke_attempts"] == 2
    flag.touch()
    assert real_mgr.cleanup() == 1
    assert real_mgr._state.leases[info.lease_id]["status"] == "revoked"
    assert real_mgr._kc.get(info.lease_id) is None
    assert real_mgr.pending_revocation() == []


def test_list_leases_has_no_side_effects(real_mgr, tmp_path):
    marker = tmp_path / "ran"
    info = real_mgr.acquire(name="k", ttl_seconds=60, cmd="echo FAKE", revoke_cmd=f"touch {marker}")
    real_mgr._state.leases[info.lease_id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    real_mgr._state.save(real_mgr._state_path)
    assert real_mgr.list_leases() == []
    assert not marker.exists()
    assert real_mgr._state.leases[info.lease_id]["status"] == "active"
    assert real_mgr._kc.get(info.lease_id) == "FAKE"
    assert real_mgr.pending_revocation()[0]["lease_id"] == info.lease_id


@pytest.mark.parametrize("ttl", [0, -5, 7 * 24 * 3600 + 1, 1.5, True, "60"])
def test_ttl_validated(real_mgr, ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        real_mgr.acquire(name="k", ttl_seconds=ttl, cmd="echo FAKE")
    assert real_mgr._state.leases == {}


def test_state_save_is_atomic_and_private(tmp_path, monkeypatch):
    import os
    import stat
    path = tmp_path / "state.json"
    LeaseState(leases={"a": {"status": "active"}}).save(path)

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("banto.lease.os.replace", boom)
    with pytest.raises(OSError):
        LeaseState(leases={"b": {}}).save(path)
    assert LeaseState.load(path).leases == {"a": {"status": "active"}}  # old file intact
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]  # temp removed
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_concurrent_managers_do_not_lose_updates(tmp_path, monkeypatch):
    monkeypatch.setattr("banto.lease.KeychainStore", _FakeKeychain)
    first = LeaseManager(state_path=tmp_path / "state.json")
    second = LeaseManager(state_path=tmp_path / "state.json")  # loaded before first writes
    a = first.acquire(name="a", ttl_seconds=60, cmd="echo A")
    b = second.acquire(name="b", ttl_seconds=60, cmd="echo B")
    on_disk = LeaseState.load(tmp_path / "state.json").leases
    assert {a.lease_id, b.lease_id} <= set(on_disk)


def test_cli_ttl_and_revoke_failure_exit_nonzero(tmp_path, monkeypatch, capsys):
    from banto import lease_cli
    monkeypatch.setattr("banto.lease.KeychainStore", _FakeKeychain)
    monkeypatch.setattr("banto.lease.DEFAULT_LEASE_STATE_PATH", tmp_path / "state.json")
    with pytest.raises(SystemExit) as exc:
        lease_cli.cmd_lease_acquire(["k", "--cmd", "echo FAKE", "--ttl", "-1"])
    assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        lease_cli.cmd_lease_acquire(["k", "--cmd", "echo FAKE", "--ttl", "abc"])
    assert exc.value.code == 1

    mgr = LeaseManager(state_path=tmp_path / "state.json")
    info = mgr.acquire(name="k", ttl_seconds=60, cmd="echo FAKE", revoke_cmd="false")
    monkeypatch.setattr(lease_cli, "LeaseManager", lambda: LeaseManager(state_path=tmp_path / "state.json"))
    with pytest.raises(SystemExit) as exc:
        lease_cli.cmd_lease_revoke([info.lease_id])
    assert exc.value.code == 1
    assert "FAILED" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        lease_cli.cmd_lease_cleanup([])
    assert exc.value.code == 1


def test_mcp_lease_annotations_and_pending(tmp_path, monkeypatch):
    import asyncio
    from banto import mcp_server
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert tools["banto_lease_list"].annotations.readOnlyHint is True
    assert tools["banto_lease_cleanup"].annotations.openWorldHint is True
    monkeypatch.setattr("banto.lease.KeychainStore", _FakeKeychain)
    monkeypatch.setattr("banto.lease.DEFAULT_LEASE_STATE_PATH", tmp_path / "state.json")
    mgr = LeaseManager()
    info = mgr.acquire(name="k", ttl_seconds=60, cmd="echo FAKE", revoke_cmd="false")
    mgr.revoke(info.lease_id)
    result = asyncio.run(mcp_server.banto_lease_list())
    assert result["structuredContent"]["pending_revocation"][0]["status"] == "revoke_failed"
    assert "FAKE" not in str(result)

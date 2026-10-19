"""Validate fixture exceptions without running a shell or loading Security."""
import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "legacy_fixture_runner", Path(__file__).resolve().parents[1] / "tools/legacy_tests.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_fixture_shell_allowlist_rejects_paths_and_real_values(tmp_path):
    # The original shell fixtures use unquoted paths: valid fixtures require a
    # short, space-free path. The allowlist deliberately rejects whitespace.
    with tempfile.TemporaryDirectory(prefix="banto-fixture-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        environment = {"BANTO_LEASE_VALUE": "FAKE"}
        valid = f'test -e {root}/allow && echo "$BANTO_LEASE_VALUE" >/dev/null'
        assert runner.fixture_command(["/bin/sh", "-c", valid], environment, root)
        assert runner.fixture_command(["echo", "FAKE"], {}, root)
        assert not runner.fixture_command(["echo", "unreviewed"], {}, root)
        assert not runner.fixture_command(["/bin/sh", "-c", valid], {"BANTO_LEASE_VALUE": "other"}, root)
        assert not runner.fixture_command(["touch", str(tmp_path / "ran")], environment, root)
        assert not runner.fixture_command(["/bin/sh", "-c", "security find-generic-password -w"], environment, root)
        space_root = tmp_path / "space fixture"
        invalid = f'test -e {space_root}/allow && echo "$BANTO_LEASE_VALUE" >/dev/null'
        assert not runner.fixture_command(["/bin/sh", "-c", invalid], environment, space_root)


def test_persistent_native_calls_fail_before_fake_native_is_called(tmp_path):
    native = Mock()
    violations = []
    guard = runner.guarded_native(native, violations, tmp_path)
    with pytest.raises(AssertionError, match="persistent_keychain_access_blocked"):
        guard.SecItemCopyMatching(None, None)
    with pytest.raises(AssertionError, match="non_fixture_signing_blocked"):
        guard.SecKeyCreateRandomKey(None, None)
    assert violations == ["persistent_keychain_access_blocked", "non_fixture_signing_blocked"]
    native.SecItemCopyMatching.assert_not_called()
    native.SecKeyCreateRandomKey.assert_not_called()

"""Validate fixture exceptions without running a shell or loading Security."""
import importlib.util
import os
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
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


@pytest.mark.parametrize("text", ["9.0.2", "9.1.1", "9.1", "9.10.0"])
def test_pytest_nine_line_is_accepted(text):
    assert runner.pytest_supported(text)


@pytest.mark.parametrize("text", [None, "", "9", "9.0.1", "8.9.9", "10.0.0", "10", "9.0.2rc1", "9.1.1+local"])
def test_pytest_outside_the_nine_line_is_blocked(text):
    assert not runner.pytest_supported(text)


def test_signing_guard_accepts_only_the_checkout_helpers(tmp_path):
    repo = tmp_path / "checkout"
    checkout_helper = repo / "core/banto_core/_sealed_testing.py"
    legacy_helper = repo / "banto/sealed_signer.py"
    installed = tmp_path / "site-packages/banto_core/_sealed_testing.py"
    assert runner.signing_caller_allowed(checkout_helper, "ephemeral_software_sign", repo)
    assert runner.signing_caller_allowed(legacy_helper, "_ephemeral_software_sign", repo)
    assert not runner.signing_caller_allowed(installed, "ephemeral_software_sign", repo)
    assert not runner.signing_caller_allowed(checkout_helper, "sign", repo)
    assert not runner.signing_caller_allowed(checkout_helper, "_ephemeral_software_sign", repo)


@pytest.fixture
def pure_check(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("this test must never start a process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    with tempfile.TemporaryDirectory(prefix="banto-guard-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        repo = tmp_path.resolve()
        environment = {"PATH": os.defpath + ":/usr/sbin:/sbin"}

        def check(executable, argv, *, cwd=None, env=None, inherited=None, current=None):
            return runner.subprocess_allowed(executable, argv, cwd, env,
                fixture_root=root, repo=repo, python_executable=sys.executable,
                inherited_environment=environment if inherited is None else inherited,
                inherited_cwd=repo if current is None else current)

        yield check, root, repo, environment


@pytest.mark.parametrize("component", [
    "$(printf${IFS}BYPASS)", "`printf${IFS}BYPASS`", "x;false", "x&false", "x|false",
    "x>out", "x<in", "x*", "x?", "x[ab]", "x\\y", "x'y", 'x"y', "x y", "x\ny", "日本語",
    "..", ".", "x/../y", "x//y",
])
def test_shell_path_slot_rejects_metacharacters_and_traversal(pure_check, component):
    _check, root, _repo, environment = pure_check
    for template, filename in (
        ('test -e {} && echo "$BANTO_LEASE_VALUE" >/dev/null', "allow"),
        ('sh -c \'true\' && printf %s "$BANTO_LEASE_VALUE" > {}', "revoked.txt"),
    ):
        command = template.format(f"{root}/{component}/{filename}")
        assert not runner.fixture_command(["/bin/sh", "-c", command], environment, root)
    assert not runner.fixture_command(["touch", f"{root}/{component}/ran"], environment, root)


def test_shell_path_slot_requires_absolute_fixed_name_and_no_symlinks(pure_check):
    _check, root, _repo, environment = pure_check
    for value in ("relative/allow", str(root / "other"), str(root.parent / "allow"),
                  "/" + str(root / "allow")):
        command = f'test -e {value} && echo "$BANTO_LEASE_VALUE" >/dev/null'
        assert not runner.fixture_command(["/bin/sh", "-c", command], environment, root)
    (root / "alias").symlink_to(root, target_is_directory=True)
    command = f'test -e {root}/alias/allow && echo "$BANTO_LEASE_VALUE" >/dev/null'
    assert not runner.fixture_command(["/bin/sh", "-c", command], environment, root)


def test_reviewed_commands_keep_working_as_pure_predicates(pure_check):
    check, root, repo, environment = pure_check
    for name, arguments in (("echo", ["FAKE"]), ("false", []), ("touch", [str(root / "ran")])):
        assert check(name, [name, *arguments])
        absolute = str(runner._fixture_guards.SYSTEM_TOOLS[name])
        assert check(absolute, [absolute, *arguments])
    shell = f'test -e {root}/allow && echo "$BANTO_LEASE_VALUE" >/dev/null'
    env = {**environment, "BANTO_LEASE_VALUE": "FAKE"}
    assert check("/bin/sh", ["/bin/sh", "-c", shell], env=env)
    assert check("sh", ["sh", "-c", shell], env=env)
    shell = f'sh -c \'true\' && printf %s "$BANTO_LEASE_VALUE" > {root}/revoked.txt'
    assert check("/bin/sh", ["/bin/sh", "-c", shell], env=env)
    for flag in ("--version", "-V"):
        assert check(sys.executable, [sys.executable, "-m", "banto", flag], cwd=repo)
    assert check("git", ["git", "-c", "core.fsmonitor=false", "check-ignore", "--quiet", "--", ".env"], cwd=root)
    assert check("/usr/bin/git", ["/usr/bin/git", "-c", "core.fsmonitor=false", "check-ignore", ".env"], cwd=root)


@pytest.mark.parametrize("name,args", [("echo", ["FAKE"]), ("false", []),
    ("git", ["-c", "core.fsmonitor=false", "check-ignore", ".env"]), ("sh", ["-c", "false"])])
def test_actual_executable_cannot_be_disguised_by_argv(pure_check, name, args):
    check, _root, repo, _environment = pure_check
    fake = str(repo / name)
    assert not check(fake, [name, *args])
    assert not check(fake, [fake, *args])
    assert not check(name, [fake, *args])
    assert not check("./" + name, [name, *args])
    assert not check(name, ["./" + name, *args])


@pytest.mark.parametrize("key,value", [
    ("PATH", ".:/usr/bin:/bin"), ("PATH", "/usr/bin::/bin"), ("PATH", "/tmp:/usr/bin"),
    ("PATH", "/usr/bin/../bin"), ("PATH", "bin:/usr/bin"), ("PATH", ""),
    ("LD_PRELOAD", "fixture.so"), ("LD_LIBRARY_PATH", "/tmp"),
    ("DYLD_INSERT_LIBRARIES", "fixture.dylib"), ("DYLD_LIBRARY_PATH", "/tmp"),
    ("BASH_ENV", "fixture.sh"), ("ENV", "fixture.sh"), ("IFS", "/"), ("CDPATH", "/tmp"),
    ("BASH_FUNC_fixture%%", "() { false; }"),
    ("PYTHONPATH", "/tmp"), ("PYTHONHOME", "/tmp"), ("PYTHONSTARTUP", "fixture.py"),
    ("GIT_EXEC_PATH", "/tmp"), ("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_GLOBAL", "/tmp/config"),
])
def test_dangerous_effective_environment_is_rejected(pure_check, key, value):
    check, _root, _repo, environment = pure_check
    env = {**environment, key: value}
    assert not check("echo", ["echo", "FAKE"], env=env)
    assert not check("echo", ["echo", "FAKE"], inherited=env)


def test_cwd_python_and_git_arguments_are_bound(pure_check):
    check, root, repo, _environment = pure_check
    assert not check("echo", ["echo", "FAKE"], cwd=".")
    assert not check("echo", ["echo", "FAKE"], cwd=root.parent)
    assert not check("echo", ["echo", "FAKE"], current=root.parent)
    assert not check("echo", ["echo", "FAKE"], cwd=str(root / ".." / root.name))
    assert not check(sys.executable, [sys.executable, "-m", "banto", "--version"], cwd=root)
    assert not check(sys.executable, [sys.executable, "-c", "print('fixture')"])
    assert not check(str(repo / "python"), [sys.executable, "-m", "banto", "--version"])
    assert not check("git", ["git", "check-ignore", "../outside"], cwd=root)
    assert not check("git", ["git", "check-ignore", "--stdin"], cwd=root)
    assert not check("git", ["git", "-c", "core.pager=fixture", "check-ignore", ".env"])
    assert not check("echo", "echo FAKE")


def test_git_requires_fixed_fsmonitor_override(pure_check):
    check, _root, _repo, _environment = pure_check
    for arguments in (
        ["git", "check-ignore", ".env"],
        ["git", "-c", "core.fsmonitor=true", "check-ignore", ".env"],
        ["git", "-c", "core.fsmonitor=false", "-c", "core.fsmonitor=fixture", "check-ignore", ".env"],
        ["git", "-c", "core.fsmonitor=false", "check-ignore", "../outside"],
    ):
        assert not check("git", arguments)


def test_audit_callback_passes_actual_executable_to_guard(pure_check, monkeypatch):
    _check, root, repo, environment = pure_check
    fake_banto = ModuleType("banto")
    fake_banto.keychain = SimpleNamespace(_SECURITY_LIB=None)
    fake_banto.sealed_signer = SimpleNamespace(_SEC=None)
    monkeypatch.setitem(sys.modules, "banto", fake_banto)
    monkeypatch.syspath_prepend(str(repo))
    # install_guards assigns bind directly; register its original for restoration.
    monkeypatch.setattr(socket.socket, "bind", socket.socket.bind)
    callbacks = []
    monkeypatch.setattr(sys, "addaudithook", callbacks.append)
    violations = runner.install_guards(repo, root)
    assert len(callbacks) == 1
    with pytest.raises(AssertionError, match="external_subprocess_blocked"):
        callbacks[0]("subprocess.Popen", (str(repo / "echo"), ["echo", "FAKE"], repo, environment))
    assert violations == ["external_subprocess_blocked"]

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Opt-in safety wrapper for unchanged Banto tests. Default: prepare only."""

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import shlex
import re
import tempfile
import subprocess
import sys
import time

DEFAULT_REPO = Path(__file__).resolve().parents[2]
HARDWARE_TESTS = [
    "tests/test_sealed_signer.py::test_enclave_roundtrip_and_non_extractable",
    "tests/test_sealed_signer.py::test_create_duplicate_key_rejected",
]
SOFTWARE_TESTS = [
    "tests/test_sealed_signer.py::test_software_sign_roundtrip_validates_ctypes_and_verify",
    *["tests/test_ecdsa_p256.py::" + name for name in (
        "test_valid_signature_verifies", "test_tampered_message_rejected", "test_tampered_signature_rejected",
        "test_wrong_public_key_rejected", "test_malformed_inputs_return_false_not_raise",
        "test_non_minimal_der_rejected", "test_crafted_der_does_not_raise",
        "test_on_curve_rejects_out_of_range_coordinates")],
]


def hashes(repo):
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((repo / "tests").rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def safety_failure(violations, kind):
    violations.append(kind)
    raise AssertionError("SAFETY_GUARD: " + kind)


def guarded_native(target, violations, repo):
    software_helpers = {(str((repo / "banto/sealed_signer.py").resolve()), "_ephemeral_software_sign"),
                        (str((repo / "core/banto_core/_sealed_testing.py").resolve()), "ephemeral_software_sign")}
    class Guard:
        def __getattr__(self, name):
            if name.startswith(("SecItem", "SecKeychain")):
                def denied(*args, **kwargs):
                    safety_failure(violations, "persistent_keychain_access_blocked")
                return denied
            function = getattr(target, name)
            if name in {"SecKeyCreateRandomKey", "SecKeyCreateSignature"}:
                def software_only(*args, **kwargs):
                    caller = inspect.currentframe().f_back
                    if (str(Path(caller.f_code.co_filename).resolve()), caller.f_code.co_name) not in software_helpers:
                        safety_failure(violations, "non_fixture_signing_blocked")
                    return function(*args, **kwargs)
                return software_only
            return function
    return Guard()


def fixture_command(values, environment, fixture_root):
    """Only the reviewed lease fixtures; no arbitrary shell or real credentials."""
    if values in [["echo", value] for value in ("FAKE", "FAKE_LEASE_VALUE", "A", "B")]:
        return True
    if (environment or {}).get("BANTO_LEASE_VALUE") not in {"", "FAKE", "FAKE_LEASE_VALUE"}:
        return False
    if values == ["false"]:
        return True
    if len(values) == 2 and values[0] == "touch":
        path = Path(values[1]).resolve()
        return path.is_relative_to(fixture_root) and path.name == "ran"
    if len(values) == 3 and values[:2] == ["/bin/sh", "-c"]:
        for pattern, filename in (
            (r"sh -c 'true' && printf %s \"\$BANTO_LEASE_VALUE\" > (\S+)", "revoked.txt"),
            (r"test -e (\S+) && echo \"\$BANTO_LEASE_VALUE\" >/dev/null", "allow"),
        ):
            match = re.fullmatch(pattern, values[2])
            if match:
                path = Path(match[1]).resolve()
                return path.is_relative_to(fixture_root) and path.name == filename
    return False


def install_guards(repo, fixture_root):
    import socket
    sys.path.insert(0, str(repo))
    from banto import keychain, sealed_signer
    # Must precede pytest collection: skipif expressions call this probe at import.
    sealed_signer.enclave_usable = lambda: False
    violations = []
    if keychain._SECURITY_LIB is not None:
        keychain._SECURITY_LIB = guarded_native(keychain._SECURITY_LIB, violations, repo)
    if sealed_signer._SEC is not None:
        sealed_signer._SEC = guarded_native(sealed_signer._SEC, violations, repo)
    ports = set()
    original_bind = socket.socket.bind

    def unix_test_path(address):
        path = Path(os.fsdecode(address)).resolve()
        return (path.name == "b.sock" and path.parent.name.startswith("banto-")
                and path.parent.parent in {Path("/tmp"), Path("/private/tmp")})

    def bind(instance, address):
        if instance.family == socket.AF_UNIX:
            if not unix_test_path(address):
                safety_failure(violations, "non_fixture_unix_bind_blocked")
        elif instance.family in {socket.AF_INET, socket.AF_INET6}:
            if address[0] not in {"127.0.0.1", "::1", "localhost"}:
                safety_failure(violations, "non_loopback_bind_blocked")
        else:
            safety_failure(violations, "unsupported_socket_bind_blocked")
        result = original_bind(instance, address)
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            ports.add(instance.getsockname()[1])
        return result

    socket.socket.bind = bind

    def audit(event, arguments):
        if event == "socket.connect":
            instance, address = arguments
            if instance.family == socket.AF_UNIX and unix_test_path(address):
                return
            if (instance.family in {socket.AF_INET, socket.AF_INET6}
                    and address[0] in {"127.0.0.1", "::1", "localhost"} and address[1] in ports):
                return
            safety_failure(violations, "non_fixture_connection_blocked")
        if event == "socket.getaddrinfo" and arguments[0] not in {"127.0.0.1", "::1", "localhost"}:
            safety_failure(violations, "external_dns_blocked")
        if event == "subprocess.Popen":
            command = arguments[1]
            values = shlex.split(command) if isinstance(command, str) else list(command)
            if (len(values) == 4 and Path(values[0]).resolve() == Path(sys.executable).resolve()
                    and values[1:3] == ["-m", "banto"] and values[3] in {"--version", "-V"}):
                return
            if values and Path(values[0]).name == "git" and values[1:2] == ["check-ignore"]:
                return
            if fixture_command(values, arguments[3], fixture_root):
                return
            safety_failure(violations, "external_subprocess_blocked")

    sys.addaudithook(audit)
    return violations


class Results:
    def __init__(self):
        self.reports = []
        self.collection_errors = []
        self.collected = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.reports.append({"id": report.nodeid, "when": report.when, "outcome": report.outcome})
            print("TEST", report.outcome, report.nodeid, flush=True)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)
            print("COLLECTION_ERROR", report.nodeid, flush=True)


def child(args):
    repo = args.repo.resolve()
    metadata_path = args.json
    manifest_path = args.manifest
    manifest = json.loads(manifest_path.read_text())
    before = hashes(repo)
    if before != manifest["files"] or len(before) != 41 or manifest["test_file_count"] != 41:
        raise SystemExit("original 41 test files differ from manifest; not running")
    metadata = {"source_commit": manifest["source_commit"], "test_file_count": len(before),
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "tests_sha256": before, "hardware_tests": HARDWARE_TESTS,
                "hardware_test_count": len(HARDWARE_TESTS), "state": "prepared_not_executed",
                "nonpersistent_software_tests": SOFTWARE_TESTS, "nonpersistent_software_test_count": len(SOFTWARE_TESTS),
                "selected_test_paths": args.test_path or ["tests"],
                "suite_scope": "selected_legacy_files" if args.test_path else "all_legacy_tests"}
    metadata.update({"runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "python_executable": sys.executable, "pytest": version("pytest"), "mcp": version("mcp"),
                     "keychain_guard": "assertion_no_fallback", "enclave_probe": False,
                     "temporary_fixture_sockets": "permitted_tmp_banto_directories_only",
                     "environment": "isolated_HOME_TMPDIR_cache_no_parent_secrets",
                     "small_pytest_fixtures": "short_tmp_path_for_unchanged_shell_fixtures_cleaned_after_run",
                     "dependency_versions": {name: version(name) for name in
                         ("pytest", "mcp", "setuptools", "build", "ruff")}})
    if metadata["pytest"] != "9.0.2" or metadata["mcp"] is None:
        metadata.update({"state": "blocked_missing_test_dependencies", "exit_code": None, "seconds": None})
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        print(json.dumps({"state": metadata["state"], "pytest": metadata["pytest"], "mcp": metadata["mcp"]}))
        return 2
    fixtures = tempfile.TemporaryDirectory(prefix="banto-tests-", dir="/tmp")
    fixture_root = Path(fixtures.name).resolve()
    violations = install_guards(repo, fixture_root)
    import pytest
    results = Results()
    started = time.monotonic()
    os.chdir(repo)
    code = int(pytest.main([*(args.test_path or ["tests"]), "-p", "no:terminal", "-p", "no:cacheprovider",
                            "--basetemp", str(fixture_root / "pytest-tmp")], plugins=[results]))
    fixtures.cleanup()
    if violations:
        code = 1
    failures = sorted({r["id"] for r in results.reports if r["outcome"] == "failed"})
    skipped = sorted({r["id"] for r in results.reports if r["outcome"] == "skipped"})
    after = hashes(repo)
    license_unchanged = hashlib.sha256((repo / "LICENSE").read_bytes()).hexdigest() == manifest["license_sha256"]
    if after != before or not license_unchanged:
        code = 1
    metadata.update({"state": "executed", "exit_code": code, "seconds": round(time.monotonic() - started, 3),
                     "collected": results.collected,
                     "passed": sum(r["outcome"] == "passed" for r in results.reports),
                     "failure_ids": failures, "collection_error_ids": results.collection_errors,
                     "skip_ids": skipped, "skip_reasons": {name: "hardware_enclave_probe_disabled" for name in skipped
                                                           if name in HARDWARE_TESTS},
                     "safety_guard_violations": violations,
                     "tests_unchanged": after == before, "source_tests_unchanged": after == manifest["files"],
                     "license_unchanged": license_unchanged})
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({name: metadata[name] for name in ("exit_code", "seconds", "passed", "failure_ids", "collection_error_ids", "skip_ids", "tests_unchanged")}))
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_REPO / "core/compatibility-tests.json")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--test-path", action="append", default=[])
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.manifest = args.manifest.resolve()
    args.state = (args.state or args.repo / "work/legacy-test-state").resolve()
    args.json = (args.json or args.state / "result.json").resolve()
    args.log = (args.log or args.state / "pytest.log").resolve()
    for selected in args.test_path:
        path = Path(selected)
        if (path.is_absolute() or ".." in path.parts or path.parts[:1] != ("tests",)
                or path.suffix != ".py" or (args.repo / path).is_symlink()
                or not (args.repo / path).is_file()
                or not (args.repo / path).resolve().is_relative_to(args.repo / "tests")):
            parser.error("test-path must name an existing relative tests/*.py file without traversal")
    if not args.run:
        print(json.dumps({"state": "prepared_not_executed", "hardware_test_count": len(HARDWARE_TESTS),
                          "selected_test_paths": args.test_path or ["tests"]}))
        return 0
    if args.child:
        return child(args)
    state = args.state
    for name in ("home", "tmp", "cache"):
        (state / name).mkdir(parents=True, exist_ok=True)
    environment = {"PATH": os.defpath + ":/usr/sbin:/sbin", "HOME": str(state / "home"),
                   "TMPDIR": str(state / "tmp"), "XDG_CACHE_HOME": str(state / "cache"),
                   "PYTHONPYCACHEPREFIX": str(state / "cache/pycache"), "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "LANG": "C.UTF-8"}
    log_path = args.log.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    args.json.resolve().parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        command = [sys.executable, "-I", str(Path(__file__).resolve()), "--run", "--child",
                   "--repo", str(args.repo), "--manifest", str(args.manifest), "--state", str(state),
                   "--json", str(args.json), "--log", str(log_path)]
        for selected in args.test_path:
            command.extend(["--test-path", selected])
        result = subprocess.run(command, env=environment, stdout=log, stderr=log)
    print(json.dumps({"exit_code": result.returncode, "log": str(log_path)}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

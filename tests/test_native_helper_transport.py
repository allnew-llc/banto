"""Offline signed-host transport tests; never import the native signer or probe it."""
import ast
import json
import os
from pathlib import Path
import subprocess

import pytest


def transport():
    tree = ast.parse(Path(__file__).with_name("test_sealed_signer.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_signed_native_case"]
    scope = dict(json=json, os=os, Path=Path, subprocess=subprocess, pytest=pytest)
    # datetime and uuid are imported inside the executed function's module globals.
    import datetime
    import uuid
    scope.update(datetime=datetime.datetime, timezone=datetime.timezone, uuid=uuid)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<offline-helper>", "exec"), scope)
    return scope["_signed_native_case"]


class _Buf:
    def __init__(self, data=""):
        self.data = data
        self.chunks = []
        self.closed = False

    def write(self, text):
        self.chunks.append(text)

    def close(self):
        self.closed = True

    def read(self, n):
        chunk = self.data[:n]
        self.data = self.data[n:]
        return chunk


class _Proc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdin = _Buf()
        self.stdout = _Buf(stdout)
        self.killed = False
        self._done = False

    def wait(self, timeout=None):
        self._done = True
        return self.returncode

    def poll(self):
        if self.killed or self._done:
            return self.returncode
        return None

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done = True


def _popen(stdout, returncode=0):
    holder = {}

    def popen(argv, **kwargs):
        proc = _Proc(returncode, stdout)
        holder["proc"] = proc
        holder["argv"] = argv
        holder["kwargs"] = kwargs
        return proc

    return popen, holder


def test_owner_required_missing_helper_fails_without_native_probe():
    popen, holder = _popen("")
    with pytest.MonkeyPatch.context() as patch, pytest.raises(pytest.fail.Exception):
        patch.setenv("BANTO_NATIVE_OWNER_REQUIRED", "1")
        patch.delenv("BANTO_NATIVE_HELPER", raising=False)
        patch.setattr(subprocess, "Popen", popen)
        transport()("native-roundtrip")
    assert holder == {}


def test_relative_helper_is_not_executed(tmp_path):
    popen, holder = _popen("")
    with pytest.MonkeyPatch.context() as patch, pytest.raises(pytest.fail.Exception):
        patch.setenv("BANTO_NATIVE_HELPER", "helper")
        patch.setattr(subprocess, "Popen", popen)
        transport()("native-roundtrip")
    assert holder == {}


def test_helper_failure_is_a_failure_never_skip_or_native_text(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    outputs = (
        "invalid DUMMY_SECRET",
        '{"status":"failed","os_status":-34018}',
        '{"status":"native_passed","os_status":0,"cleanup_verified":false}',
    )
    for output in outputs:
        popen, _holder = _popen(output, returncode=1)
        with pytest.MonkeyPatch.context() as patch, pytest.raises(pytest.fail.Exception) as exc:
            patch.setenv("BANTO_NATIVE_HELPER", str(helper))
            patch.setattr(subprocess, "Popen", popen)
            transport()("native-roundtrip")
        assert "DUMMY_SECRET" not in str(exc.value)


def test_success_requires_cleanup_sanitizes_reply_and_drops_parent_secrets(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    output = dict(status="native_passed", os_status=0, cleanup_verified=True,
                  user_presence_required=False, secret="DUMMY_SECRET")
    popen, holder = _popen(json.dumps(output), returncode=0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BANTO_NATIVE_HELPER", str(helper))
        patch.setenv("HOME", "/tmp/owner-home")
        patch.setenv("DUMMY_API_KEY", "DUMMY_SECRET")
        patch.setenv("AWS_SECRET_ACCESS_KEY", "DUMMY_SECRET")
        patch.setattr(subprocess, "Popen", popen)
        value = transport()("native-duplicate")
    assert "secret" not in value
    request = json.loads("".join(holder["proc"].stdin.chunks))
    assert request["key_id"].startswith("compat-test-")
    assert request["operation"] == "native-duplicate"
    assert not request["key_id"].startswith("banto-")
    env = holder["kwargs"]["env"]
    assert env.get("HOME") == "/tmp/owner-home"
    assert "DUMMY_API_KEY" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert "DUMMY_SECRET" not in json.dumps(env)
    assert holder["kwargs"]["stderr"] is subprocess.DEVNULL


def test_owner_mode_rejects_a_no_presence_reply(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    output = dict(status="native_passed", os_status=0, cleanup_verified=True, user_presence_required=False)
    popen, _holder = _popen(json.dumps(output), returncode=0)
    with pytest.MonkeyPatch.context() as patch, pytest.raises(pytest.fail.Exception):
        patch.setenv("BANTO_NATIVE_HELPER", str(helper))
        patch.setenv("BANTO_NATIVE_MODE", "owner")
        patch.setattr(subprocess, "Popen", popen)
        transport()("native-roundtrip")


def test_preflight_rewrites_roundtrip_to_probe(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    output = dict(status="native_passed", os_status=0, cleanup_verified=True, user_presence_required=False)
    popen, holder = _popen(json.dumps(output), returncode=0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BANTO_NATIVE_HELPER", str(helper))
        patch.setenv("BANTO_NATIVE_MODE", "preflight")
        patch.setattr(subprocess, "Popen", popen)
        transport()("native-roundtrip")
    request = json.loads("".join(holder["proc"].stdin.chunks))
    assert request["operation"] == "native-probe"


def test_oversized_helper_stdout_is_not_parsed(tmp_path):
    helper = tmp_path / "helper"
    helper.touch()
    output = '{"status":"native_passed","secret":"DUMMY_SECRET"' + (" " * 9000)
    popen, holder = _popen(output, returncode=0)
    with pytest.MonkeyPatch.context() as patch, pytest.raises(pytest.fail.Exception) as exc:
        patch.setenv("BANTO_NATIVE_HELPER", str(helper))
        patch.setattr(subprocess, "Popen", popen)
        transport()("native-duplicate")
    assert "DUMMY_SECRET" not in str(exc.value)
    assert holder["proc"].stdin.closed
    assert holder["proc"].stdout.closed

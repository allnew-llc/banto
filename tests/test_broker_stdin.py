"""Exercise real pipes/files/PTYs with a fake client: never touch the service."""
import json
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = '''
import sys
from banto import broker
broker.CALL_STDIN_TIMEOUT = 0.8
broker.MAX_MESSAGE = 256
class Client:
    def call(self, operation, **arguments):
        return {"sent": operation, "arguments": arguments}
broker.BrokerClient = Client
broker.main()
'''


def start(stdin=subprocess.PIPE, args=("call", "fixture")):
    return subprocess.Popen([sys.executable, "-c", HARNESS, *args], cwd=ROOT,
                            stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def finish(proc):
    out, err = proc.communicate(timeout=3)
    return proc.returncode, out, err


def close_input(proc):
    proc.stdin.close()
    proc.stdin = None


def test_delayed_and_chunked_input_is_not_empty():
    proc = start()
    try:
        time.sleep(0.35)  # Longer than the old proposal's 0.2-second poll.
        proc.stdin.write(b'{"name":'); proc.stdin.flush()
        time.sleep(0.1)
        proc.stdin.write(b'"fixture"}'); proc.stdin.flush()
        close_input(proc)
        code, out, err = finish(proc)
        assert code == 0 and not err
        assert json.loads(out) == {"sent": "fixture", "arguments": {"name": "fixture"}}
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()


@pytest.mark.parametrize("payload", [b'', b' ', b'[]', b'null', b'42', b'"fixture"',
                                       b'{"private":"fixture-only-marker"',
                                       b'{} trailing-fixture-only-marker', b'{}{}',
                                       b'{"x":"\xff"}', b' ' * 257])
def test_invalid_eof_never_sends_or_echoes_input(payload):
    proc = start()
    out, err = proc.communicate(payload, timeout=3)
    assert proc.returncode == 1
    assert out == b''
    assert err == b'Broker command failed; no direct Keychain fallback. Check service and operation arguments.\n'


@pytest.mark.parametrize("payload", [b'', b'{"name":', b'{"name":"fixture"}'])
def test_open_pipe_times_out_even_after_complete_json(payload):
    proc = start()
    try:
        if payload: proc.stdin.write(payload); proc.stdin.flush()
        code = proc.wait(timeout=3)  # Keep writer open; EOF is deliberately absent.
        assert code == 1
        assert proc.stdout.read() == b''
        assert b'fixture' not in proc.stderr.read()
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()
        proc.stdin.close()


def test_explicit_empty_object_at_eof():
    proc = start()
    out, err = proc.communicate(b'{}', timeout=3)
    assert proc.returncode == 0 and not err
    assert json.loads(out)['arguments'] == {}


def test_redirected_regular_file(tmp_path):
    path = tmp_path / 'arguments.json'
    path.write_bytes('{"name":"日本語"}'.encode())
    with path.open('rb') as stream:
        code, out, err = finish(start(stdin=stream))
    assert code == 0 and not err
    assert json.loads(out)['arguments']['name'] == '日本語'


def test_tty_fails_without_waiting_for_input():
    master, slave = pty.openpty()
    try:
        proc = start(stdin=slave)
        assert proc.wait(timeout=0.7) == 1
        assert proc.stdout.read() == b''
    finally:
        os.close(master); os.close(slave)
        if 'proc' in locals() and proc.poll() is None: proc.kill(); proc.wait()


def test_health_does_not_read_open_stdin():
    proc = start(args=("health",))
    try:
        assert proc.wait(timeout=0.7) == 0
        assert json.loads(proc.stdout.read()) == {"sent": "health", "arguments": {}}
    finally:
        proc.stdin.close()
        if proc.poll() is None: proc.kill(); proc.wait()


@pytest.mark.parametrize('verify', [False, True])
def test_audit_keeps_local_behavior(monkeypatch, tmp_path, capsys, verify):
    from banto import broker
    monkeypatch.setattr(broker, 'audit_path', lambda: tmp_path / 'audit.jsonl')
    monkeypatch.setattr(broker, '_read_call_arguments', lambda: pytest.fail('audit read stdin'))
    monkeypatch.setattr(broker, 'BrokerClient', lambda: pytest.fail('audit contacted service'))
    monkeypatch.setattr(sys, 'argv', ['broker', 'audit'] + (['--verify'] if verify else ['--tail', '2']))
    if verify:
        with pytest.raises(SystemExit) as exc: broker.main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out)['ok']
    else:
        broker.main()


def test_production_deadline_bounds_silent_pipe():
    code = HARNESS.replace('broker.CALL_STDIN_TIMEOUT = 0.8\n', '')
    proc = subprocess.Popen([sys.executable, '-c', code, 'call', 'fixture'], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    began = time.monotonic()
    try:
        assert proc.wait(timeout=7) == 1
        assert 4.5 <= time.monotonic() - began < 7
        assert proc.stdout.read() == b''
    finally:
        proc.stdin.close()
        if proc.poll() is None: proc.kill(); proc.wait()


def test_deeply_nested_json_fails_closed_without_echo():
    code = HARNESS.replace("broker.MAX_MESSAGE = 256\n", "broker.MAX_MESSAGE = 30000\n")
    payload = b"[" * 10000 + b"]" * 10000
    proc = subprocess.Popen([sys.executable, "-c", code, "call", "fixture"], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(payload, timeout=5)
    assert proc.returncode == 1
    assert out == b""
    assert err == b"Broker command failed; no direct Keychain fallback. Check service and operation arguments.\n"
    assert payload not in err


@pytest.mark.parametrize('size, expected', [(256, 0), (257, 1)])
def test_valid_object_size_boundary(size, expected):
    payload = b'{"x":"' + b'a' * (size - 8) + b'"}'
    assert len(payload) == size
    proc = start()
    out, err = proc.communicate(payload, timeout=3)
    assert proc.returncode == expected
    if expected:
        assert out == b'' and payload not in err
    else:
        assert len(json.loads(out)['arguments']['x']) == size - 8

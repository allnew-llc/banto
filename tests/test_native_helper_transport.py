"""Offline signed-host transport tests; never import the native signer or probe it."""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import uuid
from unittest.mock import patch

import pytest


def transport():
    tree = ast.parse(Path(__file__).with_name('test_sealed_signer.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_signed_native_case']
    scope = dict(json=json, os=os, Path=Path, subprocess=subprocess, datetime=datetime,
                 timezone=timezone, uuid=uuid, pytest=pytest)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<offline-helper>', 'exec'), scope)
    return scope['_signed_native_case']


def test_owner_required_missing_helper_fails_without_native_probe():
    with patch.dict(os.environ, {'BANTO_NATIVE_OWNER_REQUIRED': '1'}, clear=True), \
         patch.object(subprocess, 'run') as run, pytest.raises(pytest.fail.Exception):
        transport()('native-roundtrip')
    run.assert_not_called()


def test_helper_failure_is_a_failure_never_skip_or_native_text(tmp_path):
    helper = tmp_path / 'helper'
    helper.touch()
    for output in ('invalid DUMMY_SECRET', '{"status":"failed","os_status":-34018}',
                   '{"status":"native_passed","os_status":0,"cleanup_verified":false}'):
        with patch.dict(os.environ, {'BANTO_NATIVE_HELPER': str(helper)}, clear=True), \
             patch.object(subprocess, 'run', return_value=SimpleNamespace(
                 returncode=1, stdout=output, stderr='DUMMY_SECRET')), \
             pytest.raises(pytest.fail.Exception) as exc:
            transport()('native-roundtrip')
        assert 'DUMMY_SECRET' not in str(exc.value)


def test_success_requires_cleanup_and_sanitizes_reply(tmp_path):
    helper = tmp_path / 'helper'
    helper.touch()
    output = dict(status='native_passed', os_status=0, cleanup_verified=True, user_presence_required=False, secret='DUMMY_SECRET')
    with patch.dict(os.environ, {'BANTO_NATIVE_HELPER': str(helper)}, clear=True), \
         patch.object(subprocess, 'run', return_value=SimpleNamespace(
             returncode=0, stdout=json.dumps(output), stderr='DUMMY_SECRET')) as run:
        value = transport()('native-duplicate')
    assert 'secret' not in value
    request = json.loads(run.call_args.kwargs['input'])
    assert request['key_id'].startswith('compat-test-')
    assert request['operation'] == 'native-duplicate'
    assert not request['key_id'].startswith('banto-')


def test_owner_mode_rejects_a_no_presence_reply(tmp_path):
    helper = tmp_path / 'helper'
    helper.touch()
    output = dict(status='native_passed', os_status=0, cleanup_verified=True, user_presence_required=False)
    with patch.dict(os.environ, {'BANTO_NATIVE_HELPER': str(helper), 'BANTO_NATIVE_MODE': 'owner'}, clear=True), \
         patch.object(subprocess, 'run', return_value=SimpleNamespace(
             returncode=0, stdout=json.dumps(output), stderr='')), \
         pytest.raises(pytest.fail.Exception):
        transport()('native-roundtrip')

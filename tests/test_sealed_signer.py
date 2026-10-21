"""Secure Enclave sealed-signer tests.

The software-key path (ctypes + representation + signing) runs everywhere on macOS.
The true Secure Enclave roundtrip is skipped when the environment cannot create an
enclave key (headless session / missing entitlement) — the operator verifies that
interactively.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
import uuid

import pytest

from banto import ecdsa_p256, sealed_signer

pytestmark = pytest.mark.skipif(not sealed_signer.available(), reason="macOS Security framework required")


def test_key_id_validation():
    for bad in ("", "A", "with space", "x" * 100, "../escape", "UPPER"):
        with pytest.raises(sealed_signer.SealedSignerError):
            sealed_signer._validate_key_id(bad)
    for good in ("tcb-reviewer-1", "a", "a.b_c-1"):
        sealed_signer._validate_key_id(good)  # no raise


def test_software_sign_roundtrip_validates_ctypes_and_verify():
    msg = b"canonical reviewer claim"
    pub, der = sealed_signer._ephemeral_software_sign(msg)
    assert len(pub) == 65 and pub[0] == 0x04
    assert ecdsa_p256.verify(pub, msg, der) is True
    # ECDSA is randomized: a second signature differs but also verifies.
    _pub2, der2 = sealed_signer._ephemeral_software_sign(msg)
    assert der != der2


def test_payload_must_be_bytes():
    with pytest.raises(sealed_signer.SealedSignerError):
        sealed_signer.sign("whatever", "not-bytes")  # type: ignore[arg-type]


def _signed_native_case(operation):
    """Explicit signed-host path. No probe or access to any banto-* key here.

    The owner runner verifies and pins this host before setting the environment.
    This proves the helper native path, not the unsigned Python ctypes path.
    The helper receives an allowlisted environment and a capped stdout so parent
    credentials are not inherited and helper output is not parsed or echoed.
    """
    stdout_max = 8192
    helper = os.environ.get("BANTO_NATIVE_HELPER")
    if not helper:
        if os.environ.get("BANTO_NATIVE_OWNER_REQUIRED") == "1":
            pytest.fail("verified signed native helper required", pytrace=False)
        return None
    if not Path(helper).is_absolute() or not Path(helper).is_file():
        pytest.fail("signed native helper unavailable", pytrace=False)
    key_id = "compat-test-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz-") + uuid.uuid4().hex[:16]
    if operation not in {"native-roundtrip", "native-duplicate"}:
        pytest.fail("native operation forbidden", pytrace=False)
    owner_presence = operation == "native-roundtrip" and os.environ.get("BANTO_NATIVE_MODE") != "preflight"
    if operation == "native-roundtrip" and not owner_presence:
        operation = "native-probe"
    request = json.dumps({"operation": operation, "key_id": key_id})
    allowed_env = {}
    for key in ("HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME"):
        value = os.environ.get(key)
        if isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value:
            allowed_env[key] = value
    proc = None
    failed = False
    reply = None
    returncode = None
    try:
        proc = subprocess.Popen(
            [helper], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=allowed_env)
        proc.stdin.write(request)
        proc.stdin.close()
        stdout = proc.stdout.read(stdout_max + 1)
        if len(stdout) > stdout_max:
            proc.stdout.close()
            proc.wait(timeout=30)
            failed = True
        else:
            returncode = proc.wait(timeout=180)
            reply = json.loads(stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        failed = True
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
    # Fail outside the except so a helper payload cannot chain into the report.
    if failed or not isinstance(reply, dict):
        pytest.fail("signed native helper IPC failed", pytrace=False)
    valid = (returncode == 0 and reply.get("status") == "native_passed"
             and type(reply.get("os_status")) is int and reply.get("os_status") == 0
             and reply.get("user_presence_required") is owner_presence
             and reply.get("cleanup_verified") is True)
    if not valid:
        pytest.fail("signed native case failed (no skip)", pytrace=False)
    allowed = {"user_presence_required", "status", "os_status", "cleanup_verified", "non_extractable", "sealed", "curve",
               "duplicate_rejected", "public_key_x963_hex", "signature_der_hex"}
    return {k: v for k, v in reply.items() if k in allowed}


def test_enclave_roundtrip_and_non_extractable():
    reply = _signed_native_case("native-roundtrip")
    if reply is not None:
        assert reply.get("non_extractable") is True
        assert reply.get("sealed") is True and reply.get("curve") == "P-256"
        public = bytes.fromhex(reply["public_key_x963_hex"])
        signature = bytes.fromhex(reply["signature_der_hex"])
        message = b"Banto Phase 3 compatibility only; no approval authority."
        assert ecdsa_p256.verify(public, message, signature) is True
        assert ecdsa_p256.verify(public, message + b"tampered", signature) is False
        return
    if not sealed_signer.enclave_usable():
        pytest.skip("Secure Enclave not usable in this session")
    key_id = "banto-tcb-selftest"
    try:
        sealed_signer.delete_signing_key(key_id)
    except sealed_signer.SealedSignerError:
        pass
    info = sealed_signer.create_signing_key(key_id, require_user_presence=False)
    try:
        assert info["sealed"] is True and info["curve"] == "P-256"
        pub = sealed_signer.export_public_key(key_id)
        assert pub["public_key_x963_hex"] == info["public_key_x963_hex"]

        msg = b"v5 tcb rotation receipt payload"
        der = sealed_signer.sign(key_id, msg)
        assert ecdsa_p256.verify(bytes.fromhex(pub["public_key_x963_hex"]), msg, der) is True

        # The private key must be NON-EXTRACTABLE: asking the keychain for its data fails.
        # One pool owns every object this query creates. Drain it on both paths.
        # A non-null copy result is caller-owned; release it without reading bytes.
        pool = []
        out = ctypes.c_void_p()
        try:
            query = sealed_signer._cfdict([
                (sealed_signer._const(sealed_signer._SEC, "kSecClass"), sealed_signer._const(sealed_signer._SEC, "kSecClassKey")),
                (sealed_signer._const(sealed_signer._SEC, "kSecAttrApplicationTag"), sealed_signer._cfdata(sealed_signer._tag(key_id), pool)),
                (sealed_signer._const(sealed_signer._SEC, "kSecReturnData"), sealed_signer._const(sealed_signer._CF, "kCFBooleanTrue")),
            ], pool)
            status = sealed_signer._SEC.SecItemCopyMatching(query, ctypes.byref(out))
            assert not out and status != 0  # no private-key bytes returned
        finally:
            if out:
                sealed_signer._CF.CFRelease(out)
            sealed_signer._drain(pool)
    finally:
        sealed_signer.delete_signing_key(key_id)


def test_create_duplicate_key_rejected():
    reply = _signed_native_case("native-duplicate")
    if reply is not None:
        assert reply.get("duplicate_rejected") is True
        return
    if not sealed_signer.enclave_usable():
        pytest.skip("Secure Enclave not usable in this session")
    key_id = "banto-dup-selftest"
    try:
        sealed_signer.delete_signing_key(key_id)
    except sealed_signer.SealedSignerError:
        pass
    sealed_signer.create_signing_key(key_id, require_user_presence=False)
    try:
        with pytest.raises(sealed_signer.SealedSignerError):
            sealed_signer.create_signing_key(key_id, require_user_presence=False)
    finally:
        sealed_signer.delete_signing_key(key_id)

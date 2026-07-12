"""Secure Enclave sealed-signer tests.

The software-key path (ctypes + representation + signing) runs everywhere on macOS.
The true Secure Enclave roundtrip is skipped when the environment cannot create an
enclave key (headless session / missing entitlement) — the operator verifies that
interactively.
"""

from __future__ import annotations

import ctypes

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


@pytest.mark.skipif(not sealed_signer.enclave_usable(), reason="Secure Enclave not usable in this session")
def test_enclave_roundtrip_and_non_extractable():
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
        query = sealed_signer._cfdict([
            (sealed_signer._const(sealed_signer._SEC, "kSecClass"), sealed_signer._const(sealed_signer._SEC, "kSecClassKey")),
            (sealed_signer._const(sealed_signer._SEC, "kSecAttrApplicationTag"), sealed_signer._cfdata(sealed_signer._tag(key_id))),
            (sealed_signer._const(sealed_signer._SEC, "kSecReturnData"), sealed_signer._const(sealed_signer._CF, "kCFBooleanTrue")),
        ])
        out = ctypes.c_void_p()
        status = sealed_signer._SEC.SecItemCopyMatching(query, ctypes.byref(out))
        sealed_signer._CF.CFRelease(query)
        assert not out and status != 0  # no private-key bytes returned
    finally:
        sealed_signer.delete_signing_key(key_id)


def test_create_duplicate_key_rejected():
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

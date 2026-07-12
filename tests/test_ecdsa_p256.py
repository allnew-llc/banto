"""ECDSA-P256 verify tests (dependency-free), against real macOS-produced signatures."""

from __future__ import annotations

import pytest

from banto import ecdsa_p256, sealed_signer

pytestmark = pytest.mark.skipif(not sealed_signer.available(), reason="macOS Security framework required")


def _pub_and_sig(message: bytes):
    return sealed_signer._ephemeral_software_sign(message)


def test_valid_signature_verifies():
    msg = b"v5 tcb rotation canonical claim"
    pub, der = _pub_and_sig(msg)
    assert ecdsa_p256.verify(pub, msg, der) is True
    assert ecdsa_p256.verify(ecdsa_p256.public_key_from_x963(pub), msg, der) is True  # (x,y) form


def test_tampered_message_rejected():
    pub, der = _pub_and_sig(b"original")
    assert ecdsa_p256.verify(pub, b"tampered", der) is False


def test_tampered_signature_rejected():
    pub, der = _pub_and_sig(b"m")
    bad = bytearray(der)
    bad[-1] ^= 0x01
    assert ecdsa_p256.verify(pub, b"m", bytes(bad)) is False


def test_wrong_public_key_rejected():
    pub1, der = _pub_and_sig(b"m")
    pub2, _ = _pub_and_sig(b"other")
    assert ecdsa_p256.verify(pub2, b"m", der) is False


def test_malformed_inputs_return_false_not_raise():
    pub, _ = _pub_and_sig(b"m")
    assert ecdsa_p256.verify(pub, b"m", b"") is False
    assert ecdsa_p256.verify(pub, b"m", b"\x30\x00") is False
    assert ecdsa_p256.verify(pub, b"m", b"not-der") is False
    assert ecdsa_p256.verify(b"\x04" + b"\x00" * 63, b"m", b"\x30\x06\x02\x01\x01\x02\x01\x01") is False  # off-curve pubkey


def test_public_key_parsing_rejects_bad_input():
    with pytest.raises(ValueError):
        ecdsa_p256.public_key_from_x963(b"\x04\x00")  # too short
    with pytest.raises(ValueError):
        ecdsa_p256.public_key_from_x963(b"\x02" + b"\x00" * 64)  # not uncompressed
    with pytest.raises(ValueError):
        ecdsa_p256.public_key_from_x963(b"\x04" + b"\x11" * 64)  # off curve


def test_non_minimal_der_rejected():
    # A structurally-valid but non-canonical DER (leading zero on a small int) is rejected.
    pub, _ = _pub_and_sig(b"m")
    non_minimal = b"\x30\x08\x02\x02\x00\x01\x02\x02\x00\x01"
    assert ecdsa_p256.verify(pub, b"m", non_minimal) is False

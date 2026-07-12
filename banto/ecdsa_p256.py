"""Dependency-free ECDSA verification on NIST P-256 (secp256r1), SHA-256.

Verifies signatures produced by the Secure Enclave sealed signer (``sealed_signer``)
without any third-party crypto library — the same posture as the v5 TCB verifier
that consumes these signatures. Verify-only: no signing, no key material.
"""

from __future__ import annotations

import hashlib

# NIST P-256 domain parameters.
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = (-3) % _P
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_G = (_GX, _GY)


def _inv(a: int, m: int) -> int:
    return pow(a % m, -1, m)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p == q:
        m = (3 * x1 * x1 + _A) * _inv(2 * y1, _P) % _P
    else:
        m = (y2 - y1) * _inv(x2 - x1, _P) % _P
    x3 = (m * m - x1 - x2) % _P
    return (x3, (m * (x1 - x3) - y1) % _P)


def _mul(k: int, p):
    r = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def _on_curve(point) -> bool:
    x, y = point
    return (y * y - (x * x * x + _A * x + _B)) % _P == 0


_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B


def public_key_from_x963(raw: bytes):
    """Parse an uncompressed X9.63 public key (0x04 || X || Y) into (x, y)."""
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("expected a 65-byte uncompressed P-256 public key (0x04||X||Y)")
    point = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:65], "big"))
    if not (0 <= point[0] < _P and 0 <= point[1] < _P) or not _on_curve(point):
        raise ValueError("public key point is not on the P-256 curve")
    return point


def _parse_der_sig(der: bytes) -> tuple[int, int]:
    """Parse a DER ECDSA signature: SEQUENCE { INTEGER r, INTEGER s }."""
    if len(der) < 8 or der[0] != 0x30:
        raise ValueError("not a DER SEQUENCE")
    if der[1] & 0x80:  # reject long-form/indefinite length (a short ECDSA sig never needs it)
        raise ValueError("unexpected DER length form")
    if der[1] != len(der) - 2:
        raise ValueError("DER length mismatch")
    i = 2

    def _int(idx: int) -> tuple[int, int]:
        if der[idx] != 0x02:
            raise ValueError("expected DER INTEGER")
        length = der[idx + 1]
        if length == 0 or length & 0x80:
            raise ValueError("bad DER INTEGER length")
        body = der[idx + 2: idx + 2 + length]
        if len(body) != length:
            raise ValueError("truncated DER INTEGER")
        if body[0] & 0x80:
            raise ValueError("negative DER INTEGER")
        if length > 1 and body[0] == 0x00 and not (body[1] & 0x80):
            raise ValueError("non-minimal DER INTEGER")
        return int.from_bytes(body, "big"), idx + 2 + length

    r, i = _int(i)
    s, i = _int(i)
    if i != len(der):
        raise ValueError("trailing bytes after DER signature")
    return r, s


def verify(public_key, message: bytes, der_signature: bytes) -> bool:
    """Return True iff ``der_signature`` is a valid ECDSA-P256/SHA-256 signature.

    ``public_key`` is either the 65-byte X9.63 bytes or an (x, y) tuple.
    Strict DER parsing; malformed input returns False, never raises.
    """
    try:
        point = public_key_from_x963(public_key) if isinstance(public_key, (bytes, bytearray)) else public_key
        x, y = point
        if not (0 <= x < _P and 0 <= y < _P) or not _on_curve(point):
            return False
        r, s = _parse_der_sig(bytes(der_signature))
        if not (1 <= r < _N and 1 <= s < _N):
            return False
        e = int.from_bytes(hashlib.sha256(message).digest(), "big")
        w = _inv(s, _N)
        u1 = e * w % _N
        u2 = r * w % _N
        result = _add(_mul(u1, _G), _mul(u2, point))
        return result is not None and result[0] % _N == r
    except (ValueError, TypeError, ZeroDivisionError):
        return False

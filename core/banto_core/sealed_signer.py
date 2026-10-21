# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Non-extractable Secure Enclave P-256 signing (macOS Security framework).
User presence requires Touch ID OR passcode. GUI control is outside this boundary.
Presence-free keys remain operator-only. Default banto namespace is unchanged.
"""

from __future__ import annotations

import ctypes
from contextvars import ContextVar
import sys
import ctypes.util
import re
from typing import Any

TAG_PREFIX = b"com.allnew.banto.sealed."
_ACTIVE_PREFIX = ContextVar("banto_core_sealed_prefix", default=None)
KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# SecAccessControlCreateFlags (numeric bitmask, from <Security/SecAccessControl.h>)
_kUserPresence = 1 << 0
_kPrivateKeyUsage = 1 << 30
_kCFNumberIntType = 9

class SealedSignerError(RuntimeError):
    """Raised for any Secure Enclave / signing failure."""

def _load(name: str):
    path = ctypes.util.find_library(name)
    if not path:
        raise SealedSignerError(f"cannot locate the {name} framework (macOS only)")
    return ctypes.cdll.LoadLibrary(path)

try:
    _CF = _load("CoreFoundation")
    _SEC = _load("Security")
except SealedSignerError:  # pragma: no cover - non-macOS import guard
    _CF = None
    _SEC = None

def available() -> bool:
    return _CF is not None and _SEC is not None

def _configure() -> None:
    """Set restypes/argtypes so 64-bit pointers are not truncated to int."""
    p = ctypes.c_void_p
    _CF.CFStringCreateWithCString.restype = p
    _CF.CFStringCreateWithCString.argtypes = [p, ctypes.c_char_p, ctypes.c_uint32]
    _CF.CFDataCreate.restype = p
    _CF.CFDataCreate.argtypes = [p, ctypes.c_char_p, ctypes.c_long]
    _CF.CFDataGetBytePtr.restype = p
    _CF.CFDataGetBytePtr.argtypes = [p]
    _CF.CFDataGetLength.restype = ctypes.c_long
    _CF.CFDataGetLength.argtypes = [p]
    _CF.CFNumberCreate.restype = p
    _CF.CFNumberCreate.argtypes = [p, ctypes.c_int, p]
    _CF.CFDictionaryCreate.restype = p
    _CF.CFDictionaryCreate.argtypes = [p, p, p, ctypes.c_long, p, p]
    _CF.CFRelease.argtypes = [p]
    _CF.CFErrorCopyDescription.restype = p
    _CF.CFErrorCopyDescription.argtypes = [p]
    _CF.CFStringGetCString.restype = ctypes.c_bool
    _CF.CFStringGetCString.argtypes = [p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]

    _SEC.SecKeyCreateRandomKey.restype = p
    _SEC.SecKeyCreateRandomKey.argtypes = [p, ctypes.POINTER(p)]
    _SEC.SecKeyCopyPublicKey.restype = p
    _SEC.SecKeyCopyPublicKey.argtypes = [p]
    _SEC.SecKeyCopyExternalRepresentation.restype = p
    _SEC.SecKeyCopyExternalRepresentation.argtypes = [p, ctypes.POINTER(p)]
    _SEC.SecKeyCreateSignature.restype = p
    _SEC.SecKeyCreateSignature.argtypes = [p, p, p, ctypes.POINTER(p)]
    _SEC.SecItemCopyMatching.restype = ctypes.c_int32
    _SEC.SecItemCopyMatching.argtypes = [p, ctypes.POINTER(p)]
    _SEC.SecItemDelete.restype = ctypes.c_int32
    _SEC.SecItemDelete.argtypes = [p]
    _SEC.SecAccessControlCreateWithFlags.restype = p
    _SEC.SecAccessControlCreateWithFlags.argtypes = [p, p, ctypes.c_uint32, ctypes.POINTER(p)]
    _CF.CFArrayGetCount.restype = ctypes.c_long
    _CF.CFArrayGetCount.argtypes = [p]
    _CF.CFArrayGetValueAtIndex.restype = p
    _CF.CFArrayGetValueAtIndex.argtypes = [p, ctypes.c_long]
    _CF.CFDictionaryGetValue.restype = p
    _CF.CFDictionaryGetValue.argtypes = [p, p]

if available():
    _configure()

def _const(lib, name: str) -> ctypes.c_void_p:
    # CFStringRef/CFBooleanRef/… constants ARE pointers: read the symbol as a pointer.
    return ctypes.c_void_p.in_dll(lib, name)

def _symbol_addr(lib, name: str) -> ctypes.c_void_p:
    # Struct symbols (the CFDictionary callback vtables) are NOT pointers: pass the
    # ADDRESS of the symbol itself (a pointer TO the struct), not its first 8 bytes.
    return ctypes.c_void_p(ctypes.addressof(ctypes.c_char.in_dll(lib, name)))

# Only owned CF objects enter the release pool; never release constants or struct symbols.

def _cfstr(text: str, pool: list) -> ctypes.c_void_p:
    ref = _CF.CFStringCreateWithCString(None, text.encode("utf-8"), 0x08000100)  # kCFStringEncodingUTF8
    if not ref:
        raise SealedSignerError("CFStringCreateWithCString failed")
    ref = ctypes.c_void_p(ref)
    pool.append(ref)
    return ref

def _cfdata(data: bytes, pool: list) -> ctypes.c_void_p:
    ref = _CF.CFDataCreate(None, data, len(data))
    if not ref:
        raise SealedSignerError("CFDataCreate failed")
    ref = ctypes.c_void_p(ref)
    pool.append(ref)
    return ref

def _cfnumber(value: int, pool: list) -> ctypes.c_void_p:
    holder = ctypes.c_int(value)
    ref = _CF.CFNumberCreate(None, _kCFNumberIntType, ctypes.byref(holder))
    if not ref:  # guard: a NULL in the pool would make _drain call CFRelease(NULL) → abort
        raise SealedSignerError("CFNumberCreate failed")
    ref = ctypes.c_void_p(ref)
    pool.append(ref)
    return ref

def _cfdict(pairs: list[tuple[ctypes.c_void_p, ctypes.c_void_p]], pool: list) -> ctypes.c_void_p:
    n = len(pairs)
    keys = (ctypes.c_void_p * n)(*[k for k, _ in pairs])
    vals = (ctypes.c_void_p * n)(*[v for _, v in pairs])
    ref = _CF.CFDictionaryCreate(
        None, ctypes.cast(keys, ctypes.c_void_p), ctypes.cast(vals, ctypes.c_void_p), n,
        _symbol_addr(_CF, "kCFTypeDictionaryKeyCallBacks"), _symbol_addr(_CF, "kCFTypeDictionaryValueCallBacks"),
    )
    if not ref:
        raise SealedSignerError("CFDictionaryCreate failed")
    ref = ctypes.c_void_p(ref)
    pool.append(ref)
    return ref

def _drain(pool: list) -> None:
    """CFRelease every object we created for a call (leak-free; never touches constants)."""
    while pool:
        ref = pool.pop()
        try:
            _CF.CFRelease(ref)
        except Exception:  # pragma: no cover - defensive
            pass

def _cferror_text(err: ctypes.c_void_p) -> str:
    """Render and consume a caller-owned CFError, releasing it and its description."""
    if not err:
        return "unknown error"
    try:
        desc = _CF.CFErrorCopyDescription(err)
        if not desc:
            return "unknown error"
        buf = ctypes.create_string_buffer(1024)
        ok = _CF.CFStringGetCString(ctypes.c_void_p(desc), buf, 1024, 0x08000100)
        _CF.CFRelease(ctypes.c_void_p(desc))
        return buf.value.decode("utf-8", "replace") if ok else "unknown error"
    finally:
        _CF.CFRelease(err)

def _cfdata_bytes(data_ref: ctypes.c_void_p) -> bytes:
    ptr = _CF.CFDataGetBytePtr(data_ref)
    length = _CF.CFDataGetLength(data_ref)
    return ctypes.string_at(ptr, length)

def _tag(key_id: str) -> bytes:
    return (_ACTIVE_PREFIX.get() or TAG_PREFIX) + key_id.encode("ascii")

def _require_available() -> None:
    if not available():
        raise SealedSignerError("Secure Enclave sealed signing requires macOS + Security framework")

def _validate_key_id(key_id: str) -> None:
    if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
        raise SealedSignerError("key_id must match ^[a-z0-9][a-z0-9._-]{0,63}$")

def _copy_key_ref(key_id: str) -> ctypes.c_void_p:
    """Look up the sealed private key by application tag; caller CFReleases it."""
    pool: list = []
    try:
        query = _cfdict([
            (_const(_SEC, "kSecClass"), _const(_SEC, "kSecClassKey")),
            (_const(_SEC, "kSecAttrApplicationTag"), _cfdata(_tag(key_id), pool)),
            (_const(_SEC, "kSecAttrKeyType"), _const(_SEC, "kSecAttrKeyTypeECSECPrimeRandom")),
            (_const(_SEC, "kSecReturnRef"), _const(_CF, "kCFBooleanTrue")),
            (_const(_SEC, "kSecMatchLimit"), _const(_SEC, "kSecMatchLimitOne")),
        ], pool)
        out = ctypes.c_void_p()
        status = _SEC.SecItemCopyMatching(query, ctypes.byref(out))
    finally:
        _drain(pool)
    if status != 0 or not out:
        raise SealedSignerError(f"sealed key not found: {key_id} (OSStatus {status})")
    return out

def create_signing_key(key_id: str, *, require_user_presence: bool = True) -> dict[str, Any]:
    """Generate a NON-EXTRACTABLE EC P-256 signing key in the Secure Enclave."""
    _require_available()
    _validate_key_id(key_id)
    try:
        existing = _copy_key_ref(key_id)
    except SealedSignerError:
        existing = None
    if existing is not None:
        _CF.CFRelease(existing)
        raise SealedSignerError(f"a sealed key already exists for key_id={key_id}")

    pool: list = []
    try:
        flags = _kPrivateKeyUsage | (_kUserPresence if require_user_presence else 0)
        err = ctypes.c_void_p()
        access = _SEC.SecAccessControlCreateWithFlags(
            None, _const(_SEC, "kSecAttrAccessibleWhenUnlockedThisDeviceOnly"), flags, ctypes.byref(err),
        )
        if not access:
            raise SealedSignerError(f"SecAccessControlCreateWithFlags failed: {_cferror_text(err)}")
        access = ctypes.c_void_p(access)
        pool.append(access)

        private_attrs = _cfdict([
            (_const(_SEC, "kSecAttrIsPermanent"), _const(_CF, "kCFBooleanTrue")),
            (_const(_SEC, "kSecAttrApplicationTag"), _cfdata(_tag(key_id), pool)),
            (_const(_SEC, "kSecAttrAccessControl"), access),
        ], pool)
        # The Secure-Enclave TokenID is MANDATORY here: it is what makes the private
        # key non-extractable. There is no software-key path in this function; if the
        # enclave is unavailable, SecKeyCreateRandomKey fails and we raise (below).
        params = _cfdict([
            (_const(_SEC, "kSecAttrKeyType"), _const(_SEC, "kSecAttrKeyTypeECSECPrimeRandom")),
            (_const(_SEC, "kSecAttrKeySizeInBits"), _cfnumber(256, pool)),
            (_const(_SEC, "kSecAttrTokenID"), _const(_SEC, "kSecAttrTokenIDSecureEnclave")),
            (_const(_SEC, "kSecPrivateKeyAttrs"), private_attrs),
        ], pool)
        err = ctypes.c_void_p()
        priv = _SEC.SecKeyCreateRandomKey(params, ctypes.byref(err))
        if not priv:
            detail = _cferror_text(err)
            if "-25308" in detail or "-34018" in detail:
                raise SealedSignerError(
                    "Secure Enclave key creation needs an interactive login session (the human is "
                    "present) and/or a host entitled for keychain access — run it from your GUI "
                    f"Terminal or the code-signed banto host, not headless. ({detail})"
                )
            raise SealedSignerError(f"SecKeyCreateRandomKey failed: {detail}")
        _CF.CFRelease(ctypes.c_void_p(priv))
    finally:
        _drain(pool)
    pub = export_public_key(key_id)
    return {
        "key_id": key_id,
        "curve": "P-256",
        "alg": "ecdsa-p256-sha256",
        "sealed": True,
        "require_user_presence": require_user_presence,
        "public_key_x963_hex": pub["public_key_x963_hex"],
        "x_hex": pub["x_hex"],
        "y_hex": pub["y_hex"],
    }

def export_public_key(key_id: str) -> dict[str, Any]:
    """Return the PUBLIC key as X9.63 (0x04||X||Y) and split X/Y coordinates."""
    _require_available()
    _validate_key_id(key_id)
    priv = _copy_key_ref(key_id)
    try:
        pub = _SEC.SecKeyCopyPublicKey(priv)
        if not pub:
            raise SealedSignerError("SecKeyCopyPublicKey failed")
        pub = ctypes.c_void_p(pub)
        err = ctypes.c_void_p()
        data = _SEC.SecKeyCopyExternalRepresentation(pub, ctypes.byref(err))
        if not data:
            _CF.CFRelease(pub)
            raise SealedSignerError(f"SecKeyCopyExternalRepresentation failed: {_cferror_text(err)}")
        data = ctypes.c_void_p(data)
        raw = _cfdata_bytes(data)
        _CF.CFRelease(data)
        _CF.CFRelease(pub)
    finally:
        _CF.CFRelease(priv)
    if len(raw) != 65 or raw[0] != 0x04:
        raise SealedSignerError(f"unexpected public key representation ({len(raw)} bytes)")
    return {
        "key_id": key_id,
        "curve": "P-256",
        "alg": "ecdsa-p256-sha256",
        "public_key_x963_hex": raw.hex(),
        "x_hex": raw[1:33].hex(),
        "y_hex": raw[33:65].hex(),
    }

def sign(key_id: str, payload: bytes) -> bytes:
    """Return a DER ECDSA-P256/SHA-256 signature; macOS enforces presence policy."""
    _require_available()
    _validate_key_id(key_id)
    if not isinstance(payload, (bytes, bytearray)):
        raise SealedSignerError("payload must be bytes")
    priv = _copy_key_ref(key_id)
    pool: list = []
    try:
        data = _cfdata(bytes(payload), pool)
        err = ctypes.c_void_p()
        sig = _SEC.SecKeyCreateSignature(
            priv, _const(_SEC, "kSecKeyAlgorithmECDSASignatureMessageX962SHA256"), data, ctypes.byref(err),
        )
        if not sig:
            raise SealedSignerError(f"SecKeyCreateSignature failed: {_cferror_text(err)}")
        sig = ctypes.c_void_p(sig)
        der = _cfdata_bytes(sig)
        _CF.CFRelease(sig)
    finally:
        _drain(pool)
        _CF.CFRelease(priv)
    return der

def list_signing_keys() -> list[str]:
    """Return the key_ids of all sealed banto signing keys in the keychain."""
    _require_available()
    pool: list = []
    query = _cfdict([
        (_const(_SEC, "kSecClass"), _const(_SEC, "kSecClassKey")),
        (_const(_SEC, "kSecAttrKeyType"), _const(_SEC, "kSecAttrKeyTypeECSECPrimeRandom")),
        (_const(_SEC, "kSecReturnAttributes"), _const(_CF, "kCFBooleanTrue")),
        (_const(_SEC, "kSecMatchLimit"), _const(_SEC, "kSecMatchLimitAll")),
    ], pool)
    out = ctypes.c_void_p()
    status = _SEC.SecItemCopyMatching(query, ctypes.byref(out))
    _drain(pool)
    if status != 0 or not out:
        return []
    prefix = _ACTIVE_PREFIX.get() or TAG_PREFIX
    key_ids: list[str] = []
    try:
        count = _CF.CFArrayGetCount(out)
        tag_key = _const(_SEC, "kSecAttrApplicationTag")
        for index in range(count):
            item = _CF.CFArrayGetValueAtIndex(out, index)
            tag_ref = _CF.CFDictionaryGetValue(ctypes.c_void_p(item), tag_key)
            if not tag_ref:
                continue
            tag = _cfdata_bytes(ctypes.c_void_p(tag_ref))
            if tag.startswith(prefix):
                key_ids.append(tag[len(prefix):].decode("ascii", "replace"))
    finally:
        _CF.CFRelease(out)
    return sorted(key_ids)

def delete_signing_key(key_id: str) -> bool:
    _require_available()
    _validate_key_id(key_id)
    pool: list = []
    query = _cfdict([
        (_const(_SEC, "kSecClass"), _const(_SEC, "kSecClassKey")),
        (_const(_SEC, "kSecAttrApplicationTag"), _cfdata(_tag(key_id), pool)),
        (_const(_SEC, "kSecAttrKeyType"), _const(_SEC, "kSecAttrKeyTypeECSECPrimeRandom")),
    ], pool)
    status = _SEC.SecItemDelete(query)
    _drain(pool)
    return status == 0

def _ephemeral_software_sign(payload: bytes) -> tuple[bytes, bytes]:
    from ._sealed_testing import ephemeral_software_sign
    return ephemeral_software_sign(sys.modules[__name__], payload)

def enclave_usable() -> bool:
    from ._sealed_testing import enclave_usable as probe
    return probe(sys.modules[__name__])

class SealedSigner:
    """Independent namespace per instance; legacy module functions retain banto tags."""

    def __init__(self, *, tag_prefix: bytes = TAG_PREFIX):
        if not isinstance(tag_prefix, bytes) or len(tag_prefix) > 128 or not re.fullmatch(rb"[A-Za-z0-9._-]+\.", tag_prefix):
            raise ValueError("tag_prefix must be 2-128 ASCII namespace bytes ending in a dot")
        self.tag_prefix = tag_prefix

    def _call(self, function, *args, **kwargs):
        token = _ACTIVE_PREFIX.set(self.tag_prefix)
        try:
            return function(*args, **kwargs)
        finally:
            _ACTIVE_PREFIX.reset(token)

    def create_signing_key(self, key_id, *, require_user_presence=True):
        return self._call(create_signing_key, key_id, require_user_presence=require_user_presence)

    def export_public_key(self, key_id):
        return self._call(export_public_key, key_id)

    def sign(self, key_id, payload):
        return self._call(sign, key_id, payload)

    def list_signing_keys(self):
        return self._call(list_signing_keys)

    def delete_signing_key(self, key_id):
        return self._call(delete_signing_key, key_id)

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Legacy test-only signing probes; production code does not call these helpers."""
import ctypes

def ephemeral_software_sign(signer, payload: bytes) -> tuple[bytes, bytes]:
    """TEST-ONLY: generate an in-memory (non-enclave, non-permanent) P-256 key, sign,
    and return (x963_public_key, der_signature). Exercises the exact ctypes signing
    path without needing an interactive Secure Enclave session. Never persists a key.
    """
    _require_available = signer._require_available
    _CF, _SEC = signer._CF, signer._SEC
    _cfdict, _const = signer._cfdict, signer._const
    _cfnumber, _drain = signer._cfnumber, signer._drain
    _cferror_text, _cfdata_bytes, _cfdata = signer._cferror_text, signer._cfdata_bytes, signer._cfdata
    SealedSignerError = signer.SealedSignerError
    _require_available()
    err = ctypes.c_void_p()
    pool: list = []
    params = _cfdict([
        (_const(_SEC, "kSecAttrKeyType"), _const(_SEC, "kSecAttrKeyTypeECSECPrimeRandom")),
        (_const(_SEC, "kSecAttrKeySizeInBits"), _cfnumber(256, pool)),
    ], pool)
    priv = _SEC.SecKeyCreateRandomKey(params, ctypes.byref(err))
    _drain(pool)
    if not priv:
        raise SealedSignerError(f"software key gen failed: {_cferror_text(err)}")
    priv = ctypes.c_void_p(priv)
    try:
        pub = ctypes.c_void_p(_SEC.SecKeyCopyPublicKey(priv))
        pub_data = ctypes.c_void_p(_SEC.SecKeyCopyExternalRepresentation(pub, ctypes.byref(err)))
        x963 = _cfdata_bytes(pub_data)
        _CF.CFRelease(pub_data)
        _CF.CFRelease(pub)
        data = _cfdata(bytes(payload), pool)
        sig = ctypes.c_void_p(_SEC.SecKeyCreateSignature(
            priv, _const(_SEC, "kSecKeyAlgorithmECDSASignatureMessageX962SHA256"), data, ctypes.byref(err)))
        if not sig:
            raise SealedSignerError(f"software sign failed: {_cferror_text(err)}")
        der = _cfdata_bytes(sig)
        _CF.CFRelease(sig)
    finally:
        _drain(pool)
        _CF.CFRelease(priv)
    return x963, der

def enclave_usable(signer) -> bool:
    """True iff a Secure Enclave key can actually be created here (interactive session
    + entitlement). Creates and deletes a throwaway key; used to skip enclave tests."""
    available = signer.available
    delete_signing_key, create_signing_key = signer.delete_signing_key, signer.create_signing_key
    SealedSignerError = signer.SealedSignerError
    if not available():
        return False
    probe = "banto-enclave-probe"
    try:
        delete_signing_key(probe)
    except SealedSignerError:
        pass
    try:
        create_signing_key(probe, require_user_presence=False)
    except SealedSignerError:
        return False
    delete_signing_key(probe)
    return True

"""CLI subcommands for banto sealed signing (Secure Enclave, ECDSA-P256).

    banto sealed create-key <key_id> [--no-presence]
    banto sealed export-pubkey <key_id>          # JSON for the v5 TCB trust-roots
    banto sealed sign <key_id> <payload_file|->  # DER signature (hex) to stdout
    banto sealed verify <pubkey_x963_hex> <payload_file|-> <sig_hex>
    banto sealed list
    banto sealed delete <key_id>
"""

from __future__ import annotations

import json
import sys

from . import ecdsa_p256, sealed_signer


def _read_payload(source: str) -> bytes:
    if source == "-":
        return sys.stdin.buffer.read()
    with open(source, "rb") as handle:
        return handle.read()


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def cmd_sealed_dispatch(args: list[str]) -> None:
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    sub, rest = args[0], args[1:]
    try:
        if sub == "create-key":
            if not rest:
                _fail("usage: banto sealed create-key <key_id> [--no-presence]")
            key_id = rest[0]
            presence = "--no-presence" not in rest[1:]
            info = sealed_signer.create_signing_key(key_id, require_user_presence=presence)
            print(json.dumps(info, indent=2))
            if presence:
                print("note: this key requires Touch ID / passcode per signature.", file=sys.stderr)
        elif sub == "export-pubkey":
            if not rest:
                _fail("usage: banto sealed export-pubkey <key_id>")
            print(json.dumps(sealed_signer.export_public_key(rest[0]), indent=2))
        elif sub == "sign":
            if len(rest) < 2:
                _fail("usage: banto sealed sign <key_id> <payload_file|->")
            der = sealed_signer.sign(rest[0], _read_payload(rest[1]))
            print(der.hex())
        elif sub == "verify":
            if len(rest) < 3:
                _fail("usage: banto sealed verify <pubkey_x963_hex> <payload_file|-> <sig_hex>")
            ok = ecdsa_p256.verify(bytes.fromhex(rest[0]), _read_payload(rest[1]), bytes.fromhex(rest[2]))
            print("valid" if ok else "INVALID")
            sys.exit(0 if ok else 2)
        elif sub == "list":
            print(json.dumps(sealed_signer.list_signing_keys(), indent=2))
        elif sub == "delete":
            if not rest:
                _fail("usage: banto sealed delete <key_id>")
            print("deleted" if sealed_signer.delete_signing_key(rest[0]) else "not found")
        else:
            _fail(f"unknown sealed subcommand: {sub}")
    except sealed_signer.SealedSignerError as exc:
        _fail(str(exc))

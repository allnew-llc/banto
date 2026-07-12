# Sealed signing (Secure Enclave)

banto's vault *dispenses* API keys within a budget. Sealed signing is the opposite
guarantee: a signing key that banto **never returns**. The private key is generated
inside the Apple **Secure Enclave** and is **non-extractable** — it physically cannot
leave the enclave, so no caller (human or agent) can copy it. banto only:

- **creates** the key (inside the enclave),
- **exports the public key** (non-secret),
- **signs** a payload — raising a **Touch ID / passcode prompt per signature** when
  the key requires user presence.

That last property is the point: with a **user-presence** key, an autonomous agent
that controls the workspace **cannot mint a signature silently** — each signature
raises a Touch ID / passcode prompt that a human approves at the machine. This is
what makes a sealed key an *external root of trust* (e.g. for `ios-app-factory-v5`
TCB rotation-receipt reviewers). Read **Honest limits** below for the exact bounds of
that guarantee — it holds only for presence-required keys, presence is satisfiable by
passcode as well as biometry, and it assumes the agent cannot drive the GUI to click
the approval itself.

- Algorithm: **ECDSA P-256 / SHA-256**. Signatures are DER-encoded.
- Public key: X9.63 uncompressed (`0x04 || X || Y`), 65 bytes.
- Dependency-free (ctypes → macOS Security framework). macOS + Apple Secure Enclave only.
- Verification: `banto.ecdsa_p256.verify(...)` — dependency-free, verify-only.

## Requirements

Secure Enclave key **creation and signing require an interactive login session**
(the human is present) and a host permitted for keychain access. Run from your GUI
Terminal or the code-signed banto host — not headless (a headless call fails closed
with a clear message).

## CLI

```bash
# one-time: create a sealed reviewer key (Touch ID required per signature)
banto sealed create-key tcb-reviewer-1

# export the PUBLIC key (register this in the v5 TCB trust-roots — it is not secret)
banto sealed export-pubkey tcb-reviewer-1
# -> {"key_id":"tcb-reviewer-1","curve":"P-256","alg":"ecdsa-p256-sha256",
#     "public_key_x963_hex":"04...","x_hex":"...","y_hex":"..."}

# sign a payload (a Touch ID prompt appears; the human approves)
banto sealed sign tcb-reviewer-1 receipt-claim.bin        # -> DER signature (hex)
printf '%s' "$CANONICAL_CLAIM" | banto sealed sign tcb-reviewer-1 -

# verify (dependency-free)
banto sealed verify <pubkey_x963_hex> receipt-claim.bin <sig_hex>

banto sealed list
banto sealed delete tcb-reviewer-1
```

`create-key --no-presence` omits the per-signature prompt (still non-extractable) —
use only for automated self-tests, never for a production root of trust.

## v5 TCB rotation-reviewer runbook

To provision the external root of trust the v5 TCB rotation layer needs (two
independent reviewers):

1. On **two separate machines/people**, run `banto sealed create-key tcb-reviewer-N`
   (Touch ID required per signature). The private keys stay in each enclave.
2. `banto sealed export-pubkey tcb-reviewer-N` on each, and put the two public keys
   (with distinct `reviewer_group`s) into
   `skills/ios-app-factory-v5/tcb-rotation-trust-roots.json` `roots[]`, with
   `alg: "ecdsa-p256-sha256"`. Set `trusted_clock.status = provisioned`.
3. To admit a rotation, each reviewer signs the canonical reviewer claim with
   `banto sealed sign tcb-reviewer-N -` (approving via Touch ID). The two DER
   signatures go into the rotation receipt.
4. `v5_tcb_rotation.py` verifies both signatures against the trusted roots.

## MCP tools

`banto_sealed_create_key` (always creates a **user-presence** key — the agent has no
knob to disable the prompt), `banto_sealed_export_pubkey`, `banto_sealed_sign`,
`banto_sealed_list`. An agent can orchestrate, but `banto_sealed_sign` triggers the
human Touch ID prompt — the agent cannot complete a signature on its own.

**Deletion is intentionally NOT an MCP tool.** Deleting a reviewer key and
re-creating one under the same `key_id` is a trust-root substitution/downgrade
attack, so `delete` and `--no-presence` creation are **operator-only**, via
`banto sealed …` in an interactive terminal — never over the agent surface.

## Honest limits

- Requires Apple Secure Enclave hardware (Apple Silicon / T2). No software fallback
  by design — a software key would reintroduce the extractability this feature exists
  to remove.
- Per-signature approval is only as strong as the presence policy: `create-key` with
  user presence requires biometry/passcode; without it, any process with a keychain
  session can invoke signing (still non-extractable, but not human-gated). Use user
  presence for a real root of trust.
- **Presence ≠ biometry.** macOS satisfies user presence with the **device passcode**
  as well as Touch ID. The gate proves "someone who can unlock this Mac approved," not
  "a specific human's fingerprint." Provision reviewer keys on machines whose passcode
  the automation agent does not know.
- **The gate assumes the agent cannot drive the GUI.** If the same actor that requests
  the signature can also move the mouse / press keys on the reviewer's machine (screen
  sharing, a computer-use agent, physical access), it can approve its own prompt. Keep
  the signing machine outside the automation's control surface.
- **Substitution is fenced off, not impossible.** `delete` is removed from MCP so a
  compromised agent cannot delete-and-recreate a reviewer key under the same `key_id`.
  An operator with terminal access still can — treat `banto sealed delete` and
  `--no-presence` as privileged, and pin public keys in the v5 trust-roots so an
  unexpected key swap is at least detectable.

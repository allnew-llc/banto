# Secret Rotation Test Scenarios

This document maps the user scenarios in
`docs/secret-rotation-user-scenarios.md` to executable tests.

## Test Matrix

| ID | User scenario | Test coverage |
|---|---|---|
| T1 | S1 xAI full-auto rotation | `tests/test_sync/test_xai_rotator.py` |
| T2 | S2 OpenAI service-account rotation | `tests/test_sync/test_openai_rotator.py` |
| T3 | S3 Google API key rotation | `tests/test_sync/test_google_rotator.py` |
| T4 | S4 propagate-only flow | `tests/test_sync/test_propagation.py` |
| T5 | S5 manual-cutover block | `tests/test_sync/test_propagation.py` |
| T6 | S6 rollback and cleanup | xAI/OpenAI/Google rotator tests |
| T7 | S7 Vercel sensitive multi-env push | `tests/test_sync/test_drivers.py`, `tests/test_sync/test_sync.py` |
| T8 | S8 missing management credential | xAI/OpenAI/Google CLI/rotator tests |
| T9 | Classification and incident report | `tests/test_sync/test_capabilities.py`, `tests/test_sync/test_incident_report.py` |
| T10 | Secret value non-disclosure shape | JSON outputs expose metadata only; tests assert created key values are not emitted |
| T11 | Provider-specific manual registration UX | `tests/test_register_popup.py` |

## Required Commands

Run all focused scenario tests:

```bash
uv run pytest \
  tests/test_sync/test_xai_rotator.py \
  tests/test_sync/test_openai_rotator.py \
  tests/test_sync/test_google_rotator.py \
  tests/test_sync/test_propagation.py \
  tests/test_sync/test_drivers.py \
  tests/test_sync/test_sync.py \
  tests/test_sync/test_capabilities.py \
  tests/test_sync/test_incident_report.py \
  tests/test_sync/test_sync_state.py \
  tests/test_register_popup.py \
  -q
```

Run the full suite:

```bash
uv run pytest -q
```

Run static whitespace checks:

```bash
git diff --check
```

Verify CLI discovery:

```bash
uv run banto sync
```

## Acceptance Criteria

- T1 through T9 must pass.
- `uv run pytest -q` must pass.
- `git diff --check` must pass.
- CLI help must list `xai-api-key`, `openai-service-account`,
  `google-api-key`, `propagate`, `classify`, and `incident-report`.
- Tests must prove that provider-issued key values are handed to propagation
  but not rendered in command JSON/human output.
- Registration UI must expose provider-specific issuer guidance and batch
  registration using `provider|ENV_NAME=value`.

## Manual Live-Test Checklist

These tests are intentionally not run by CI because they create or delete real
provider credentials:

1. Create or store an xAI management key with key-management permission.
2. Run `banto sync xai-api-key xai --team-id <team_id> --dry-run`.
3. Run the same command without `--dry-run` in a non-production test project.
4. Confirm Keychain contains the new value under the configured account.
5. Confirm Vercel has a Sensitive `XAI_API_KEY` in the requested environments.
6. Confirm old provider key deletion only occurs after successful propagation.

# Secret Rotation Automation Plan

This document defines the rollout plan for incident-ready secret rotation in
`banto sync`.

## Goals

- classify every sync-managed secret before automating it
- separate low-risk propagation from high-risk cutovers
- let operators use one vocabulary across planning, CLI output, and future
  rotator adapters

## Rotation Classes

| Rotation class | Meaning | Typical action |
|---|---|---|
| `full_auto` | Issuance, storage, propagation, validation, and retirement can be scripted end-to-end | run approved rotator, validate, retire previous credential |
| `partial_auto` | Some provider lifecycle steps still need manual approval or console actions | automate inventory/validation/propagation, gate issuance or retirement |
| `propagate_only` | New value can be redistributed safely once obtained | operator obtains replacement, `banto` stores and pushes it |
| `inventory_only` | Non-secret or low-value to rotate during an incident | keep in coverage reports only |
| `manual_cutover` | Blind overwrite is unsafe because runtime behavior changes | follow a dedicated runbook |
| `review_required` | No rule exists yet | classify before automating |

## Rollout Phases

| Phase | Scope | Deliverable |
|---|---|---|
| `phase_0` | coverage, vocabulary, classification | bundled capability matrix + CLI classification |
| `phase_1` | safe execution primitives | dry-run, redaction, allowlisted rotator commands |
| `phase_2` | shared propagation flow | provider-agnostic `propagate_only` workflow |
| `phase_3` | first `full_auto` providers | OpenAI and project-managed Google keys |
| `phase_4` | selective provider adapters | Anthropic and other provider-specific adapters |
| `phase_5` | coordinated cutover flows | crypto, webhook, and app-secret runbooks |

## Current Planning Rules

- `OPENAI_API_KEY` is planned as `full_auto`, but only through service-account
  style issuance that can be scripted safely.
- `XAI_API_KEY` is planned as `full_auto` through the xAI Management API, using
  a separate management key and optional propagation checks before storage.
- `GEMINI_API_KEY` remains `partial_auto` until project-level issuance is proven
  in this workspace.
- provider secrets such as GitHub, LINE, Twilio, Zoom, and Azure start as
  `propagate_only` until a provider-specific issuance adapter is added.
- Cloudflare Account API tokens can use `banto sync cloudflare-account-token`
  when a token-creator credential is available.
- Stripe webhook signing secrets can use `banto sync stripe-webhook-endpoint`,
  but remain `manual_cutover` because endpoint routing must be coordinated.
- identifiers such as account IDs, tenant IDs, and publishable keys remain
  `inventory_only`.
- `ENCRYPTION_KEY`, `HMAC_SECRET`, `CRON_SECRET`, and webhook verification
  secrets stay in `manual_cutover`.

## Operator Workflow

Use the new classification command before prioritizing rotation work:

```bash
banto sync classify
banto sync classify --json
```

This command reads `sync.json`, matches each env var against the bundled
capability matrix, and groups it into one of the rotation classes above.

For `propagate_only`, `partial_auto`, and `full_auto` entries that already have
a replacement value, use the common propagation flow:

```bash
banto sync propagate <name> --from-cli '<command>'
banto sync propagate <name> --validate
banto sync propagate <name> --smoke-preset provider-validate
banto sync propagate <name> --dry-run
```

`propagate` does not mint new provider credentials by itself. It standardizes
the safe middle of the workflow: optional validation, Keychain update, target
sync, and optional post-sync smoke command.

For provider APIs that can issue one-time secret values without exposing them
to the agent, use native issuance adapters:

```bash
banto sync cloudflare-account-token cloudflare-api-token \
  --account-id <account_id> --policy-file cloudflare-policy.json \
  --revoke-token <old_token_id> --dry-run

banto sync stripe-webhook-endpoint stripe-test-webhook \
  --source-secret stripe-test-secret \
  --url https://example.com/api/stripe/webhook \
  --event checkout.session.completed \
  --delete-previous-endpoint <old_endpoint_id> \
  --dry-run
```

Retirement flags are explicit. Omit them during staged cutovers, then run the
same command with the previous credential or endpoint id when the new value is
verified in production.

For provider console flows that do not yet have a native API rotator, use the
browser-assisted issuance runner:

```bash
banto sync browser-issue github --recipe recipes/github-token.json --dry-run
banto sync browser-issue github --recipe recipes/github-token.json \
  --validate --smoke-preset provider-validate
```

This runner executes a local Playwright recipe, captures the newly displayed
one-time credential inside the local banto process, and immediately hands it to
the same propagation primitives. The agent receives metadata only; the issued
secret value is never printed, logged, or returned in JSON output.

Built-in smoke presets:

- `provider-validate`: re-run the provider's lightweight validation after propagation
- `env-present`: confirm the resolved secret value is non-empty

Custom shell smoke commands remain available through `--smoke`, but presets are
the recommended default for routine incident handling.

For incident triage, generate a prioritized report before touching production:

```bash
banto sync incident-report
banto sync incident-report --json
```

This report groups secrets into:

- `Rotate Now`: safe low-risk candidates such as `full_auto` and `propagate_only`
- `Approval Gated`: provider-specific confirmation still required
- `Manual Cutover`: staged rollout only
- `Monitor Only`: coverage checks but no incident rotation

## Next Implementation Targets

The first `full_auto` rotator is now available for OpenAI project-managed
`OPENAI_API_KEY` secrets:

```bash
banto sync openai-service-account openai --project-id proj_123 --dry-run
banto sync openai-service-account openai --project-id proj_123 --validate \
  --smoke-preset provider-validate
banto sync openai-service-account openai --project-id proj_123 \
  --revoke-service-account svc_old
```

Safety rules for this rotator:

- resolve the admin credential from `OPENAI_ADMIN_KEY` first, then Keychain
  account `openai-admin`
- create a new OpenAI project service account and keep the unredacted API key
  inside the local process only
- hand the new key to `banto sync propagate` primitives for validation, storage,
  target sync, and optional smoke tests
- if propagation fails, delete the newly created service account and try to
  restore the previous Keychain value
- revoke the previous service account only after the new key has propagated

Google Cloud API key rotation is now available for project-managed
`GOOGLE_API_KEY` secrets:

```bash
banto sync google-api-key google-api-key --project-id my-project --dry-run
banto sync google-api-key google-api-key --project-id my-project --validate \
  --smoke-preset provider-validate
banto sync google-api-key google-api-key --project-id my-project \
  --sync-shared-account-secrets
banto sync google-api-key google-api-key --project-id my-project \
  --revoke-key projects/my-project/locations/global/keys/old-key
```

Safety rules for this rotator:

- resolve a Google OAuth access token from `GOOGLE_OAUTH_ACCESS_TOKEN` first,
  then local Application Default Credentials via
  `gcloud auth application-default print-access-token`, then active gcloud user
  credentials via `gcloud auth print-access-token`
- create a new Google API key through the API Keys API and fetch its
  unredacted key string with `getKeyString`
- hand the new key to `banto sync propagate` primitives for validation, storage,
  target sync, and optional smoke tests
- optionally propagate the same new key to sibling secrets that share the same
  Keychain account, such as `GEMINI_API_KEY`, but only when explicitly opted in
- if propagation fails, delete the newly created Google key and try to restore
  the previous Keychain value
- revoke the previous Google key only after the new key has propagated
- Gemini validation now uses the `x-goog-api-key` header instead of placing the
  key in the URL query string

xAI API key rotation is now available for `XAI_API_KEY` secrets:

```bash
banto sync xai-api-key xai --team-id team_123 --dry-run
banto sync xai-api-key xai --team-id team_123 --wait-propagation \
  --smoke-preset provider-validate
banto sync xai-api-key xai --team-id team_123 \
  --revoke-api-key ak_old
```

Safety rules for this rotator:

- resolve the management credential from `XAI_MANAGEMENT_API_KEY` first, then
  Keychain account `xai-management`
- create a new xAI API key through `https://management-api.x.ai`
- default ACLs are `api-key:model:*` and `api-key:endpoint:*`, with `--acl`
  overrides available for narrower keys
- optionally wait for xAI API key propagation before storing the new value
- hand the new key to `banto sync propagate` primitives for validation,
  storage, target sync, and optional smoke tests
- if propagation fails, delete the newly created xAI key and try to restore the
  previous Keychain value
- revoke the previous xAI API key only after the new key has propagated

Next targets:

1. add provider adapters on top of the shared propagation flow
2. convert stable browser recipes into provider-native rotator adapters where
   provider APIs become available
3. decide whether `GEMINI_API_KEY` can be promoted from `partial_auto` to `full_auto`
4. design separate runbooks for `manual_cutover` secrets

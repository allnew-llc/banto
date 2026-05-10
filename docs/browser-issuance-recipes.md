# Browser Issuance Recipes

`banto sync browser-record`, `banto sync browser-issue`, and
`banto sync browser-revoke` automate provider console flows when a provider does
not yet have a native rotator. The recorder turns a guided browser session into
a recipe, the issuer captures a newly displayed one-time credential and hands it
directly to propagation, and the revoker retires the exposed or previous
credential after the replacement is live.

The agent still never receives the secret value. The value is held only in the
local Python process, then passed to `propagate_secret` for validation,
Keychain storage, target sync, and optional smoke checks. CLI and JSON output
contain metadata only.

## Install

```bash
python -m pip install 'banto[browser]'
python -m playwright install chromium
```

## Closed-Loop Run

```bash
banto sync browser-record github \
  --start-url https://github.com/settings/tokens \
  --output recipes/github-token.json \
  --capture-selector '[data-testid=issued-token]' \
  --exposed-key-id-selector '[data-testid=issued-token-id]' \
  --exposure-manifest-out recipes/github-token.exposure.json \
  --revoke-recipe recipes/github-token-revoke.json \
  --script-out scripts/issue-github-token.sh

banto sync browser-issue github \
  --recipe recipes/github-token.json \
  --exposure-manifest recipes/github-token.exposure.json \
  --validate --smoke-preset provider-validate

banto sync browser-revoke github \
  --recipe recipes/github-token-revoke.json \
  --key-id <provider-key-id> \
  --dry-run
```

Dry-run before live runs:

```bash
banto sync browser-record github \
  --start-url https://github.com/settings/tokens \
  --output recipes/github-token.json \
  --capture-selector '[data-testid=issued-token]' \
  --dry-run

banto sync browser-issue github --recipe recipes/github-token.json --dry-run
banto sync browser-issue github --recipe recipes/github-token.json \
  --validate --smoke-preset provider-validate
```

By default, banto opens a visible Chromium profile under
`~/.local/state/banto/browser-profiles/<provider>`. Use that profile for the
provider login session. `--headless` is available only after login, MFA, and
provider UI behavior are stable enough for unattended runs.

## Recipe Format

```json
{
  "version": 1,
  "name": "github-token-console",
  "provider": "github",
  "start_url": "https://github.com/settings/tokens",
  "steps": [
    {"action": "click", "selector": "text=Generate new token"},
    {
      "action": "fill",
      "selector": "input[name=description]",
      "text": "banto-{{secret_name}}-{{timestamp}}"
    },
    {"action": "click", "selector": "button:has-text('Generate token')"},
    {"action": "wait_for_selector", "selector": "[data-testid=issued-token]"}
  ],
  "capture": {
    "selector": "[data-testid=issued-token]",
    "source": "text",
    "min_length": 8
  },
  "metadata_selectors": {
    "key_label": "[data-testid=issued-token-label]"
  }
}
```

## Retirement Recipe Format

Retirement recipes accept only provider metadata such as key ids, endpoint ids,
service account ids, or labels. They intentionally reject raw secret-looking
values.

```json
{
  "version": 1,
  "name": "github-token-revoke",
  "provider": "github",
  "start_url": "https://github.com/settings/tokens",
  "steps": [
    {"action": "fill", "selector": "input[name=q]", "text": "{{key_id}}"},
    {"action": "click", "selector": "[data-token-id=\"{{key_id}}\"] button"},
    {"action": "click", "selector": "button:has-text('Delete')"}
  ],
  "success_selector": "[data-token-deleted=\"{{key_id}}\"]"
}
```

Supported actions:

- `click`
- `fill`
- `press`
- `select`
- `wait_for_selector`
- `wait_for_url`
- `wait_for_timeout`
- `human_checkpoint`

Supported capture sources:

- `text`
- `input`
- `attribute`

Template placeholders available in `fill.text`:

- `{{secret_name}}`
- `{{env_name}}`
- `{{provider}}`
- `{{timestamp}}`

Retirement recipes additionally support:

- `{{key_id}}`
- `{{key_label}}`

## Exposure Manifest

When a recipe authoring run creates a throwaway key, record only its provider id
or label, never the secret value:

```json
{
  "version": 1,
  "secret_name": "github",
  "provider": "github",
  "key_id": "tok_old_123",
  "key_label": "banto-github-authoring",
  "recipe": "recipes/github-token.json",
  "revoke_recipe": "recipes/github-token-revoke.json"
}
```

`banto sync browser-issue --exposure-manifest ...` issues a replacement, stores
and syncs it, runs validation/smoke checks when requested, and only then runs the
retirement recipe for the exposed key id.

## Safety Rules

- Do not paste a secret into a recipe file.
- Do not use an LLM-driven browser session to read or transcribe the issued key.
- Browser retirement must use provider key ids/labels; raw secret-looking values
  are rejected.
- Prefer provider-native rotators for OpenAI, Google, and xAI.
- Use `--dry-run` before a live run to confirm the configured secret,
  classification, targets, recipe, browser profile, and smoke settings.
- If a recipe is created by manually observing a provider console, rotate and
  revoke any key that was exposed during recipe authoring before trusting the
  recipe for production use.

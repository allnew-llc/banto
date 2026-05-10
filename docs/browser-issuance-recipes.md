# Browser Issuance Recipes

`banto sync browser-issue` automates provider console flows when a provider does
not yet have a native rotator. The browser runner can click through a saved
recipe, capture a newly issued one-time credential, and hand it directly to the
existing propagation flow.

The agent still never receives the secret value. The value is held only in the
local Python process, then passed to `propagate_secret` for validation,
Keychain storage, target sync, and optional smoke checks. CLI and JSON output
contain metadata only.

## Install

```bash
python -m pip install 'banto[browser]'
python -m playwright install chromium
```

## Run

```bash
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

## Safety Rules

- Do not paste a secret into a recipe file.
- Do not use an LLM-driven browser session to read or transcribe the issued key.
- Prefer provider-native rotators for OpenAI, Google, and xAI.
- Use `--dry-run` before a live run to confirm the configured secret,
  classification, targets, recipe, browser profile, and smoke settings.
- If a recipe is created by manually observing a provider console, rotate and
  revoke any key that was exposed during recipe authoring before trusting the
  recipe for production use.

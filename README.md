[日本語](./README.ja.md)

# banto

A budget-gated API key vault designed to limit excessive API charges caused by unexpectedly running LLM agents. API keys are stored in a secure backend (macOS Keychain by default, or 1Password / custom stores) -- not in `.env` files or environment variables -- and released only when the budget allows.

> Named after the **bantō** (番頭) — the head clerk of Edo-period Japanese merchant houses who held the keys to the storehouse and managed the account books.

## Problem

Most projects store API keys in `.env` files or environment variables, where processes running as the same user can read them. LLM agents can catch budget-check exceptions and call the API regardless. When agents run unexpectedly, this can result in significant charges.

## Solution

banto makes budget enforcement **structural**: API keys are stored in macOS Keychain and only released through banto's API when the budget allows. No budget = no key = no API call through banto.

```
Agent requests API key
        |
        v
  [Budget hold] --over--> BudgetExceededError (key never returned)
        |
       ok (estimated cost reserved)
        v
  [Keychain lookup] --> API key returned
        |
        v
  Agent calls API
        |
        v
  [Settle hold] --> Actual cost recorded, surplus budget freed
```

## Requirements

- macOS (uses Keychain for secret storage)
- Python 3.10+
- No external dependencies

## Install

```bash
pip install banto
```

Or install from source:

```bash
git clone https://github.com/allnew-llc/banto.git
cd banto
pip install -e .
```

## Quick start

### 1. Initialize config

```bash
banto init    # copies default config to ~/.config/banto/
```

### 2. Set your monthly budget

The default budget is **$0 (USD)**. Please set your own budget. While the budget remains at $0, all API key retrieval is blocked.

```bash
banto budget 50    # set global monthly limit to $50 USD
```

All budgets are denominated in **US dollars (USD)** and enforced on a **calendar month** basis. The budget resets automatically on the 1st of each month.

### 3. Store API keys

Register your API keys in macOS Keychain. Run `banto store <provider>` for each provider you use. You will be prompted to enter the key — input is masked and not displayed on screen.

```
$ banto store openai
Enter API key for 'openai':    ← paste your key here (input is hidden)
Stored 'openai' in Keychain.
```

If a key already exists for the provider, you will be asked whether to overwrite:

```
$ banto store openai
Key for 'openai' already exists. Overwrite? (y/N): y
Enter API key for 'openai':
Stored 'openai' in Keychain.
```

You can find your API keys at each provider's dashboard:

- **OpenAI**: https://platform.openai.com/api-keys
- **Google**: https://aistudio.google.com/apikey
- **Anthropic**: https://console.anthropic.com/settings/keys

Repeat for each provider:

```bash
banto store openai
banto store google
banto store anthropic
```

### 4. Use in your code

```python
from banto import SecureVault, BudgetExceededError, KeyNotFoundError

vault = SecureVault(caller="my_app")

try:
    # Budget hold + key retrieval (estimated cost reserved upfront)
    key = vault.get_key(
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=500,
    )

    # Use the key
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[...],
        api_key=key,
    )

    # Settle with actual usage (frees surplus budget)
    vault.record_usage(
        model="gpt-4o",
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        provider="openai",
        operation="chat",
    )

except BudgetExceededError as e:
    print(f"Over budget: ${e.remaining:.2f} remaining of ${e.limit:.2f}")

except KeyNotFoundError as e:
    print(f"No key for {e.provider}. Run: banto store {e.provider}")
```

## CLI

```bash
banto status              # Budget status (with per-provider/model breakdown)
banto budget [args]       # View or set budget limits
banto store <provider>    # Store API key in Keychain
banto delete <provider>   # Delete API key from Keychain
banto list                # List stored keys + budget
banto check <model> ...   # Dry-run budget check
banto init                # Initialize user config
```

### Budget management

```bash
# View all limits
banto budget

# Set global monthly limit
banto budget 100

# Set per-provider limit
banto budget --provider openai 30

# Set per-model limit
banto budget --model dall-e-3 10

# Remove a limit
banto budget --provider openai --remove
```

### Budget check examples

```bash
# Token-based model
banto check gpt-4o --tokens 1000 500

# Image generation
banto check dall-e-3 --n 4 --quality hd --size 1024x1024

# Video generation
banto check sora-2 --seconds 10
```

## Configuration

Default config is at `~/.config/banto/`. Run `banto init` to create `config.json` (budget settings) and `pricing.json` (pricing table).

### Budget limit

All budgets are in **USD**, enforced per **calendar month**. The default is `0`. Please set your own budget limit.

```json
{
  "monthly_limit_usd": 0
}
```

### Provider-to-model mapping

Maps models to providers so `get_key()` knows which Keychain entry to look up:

```json
{
  "providers": {
    "openai": {
      "models": ["gpt-4o", "dall-e-3", "sora-2"]
    },
    "google": {
      "models": ["gemini-3-pro-image-preview", "imagen-4.0-generate-001"]
    }
  }
}
```

### Pricing (`pricing.json`)

Pricing is stored in a **separate file** (`~/.config/banto/pricing.json`), independent of budget settings. banto ships with a sample pricing table covering major models from OpenAI, Anthropic, and Google as of March 2026.

> **Prices are static and not guaranteed.** banto does not fetch pricing from provider APIs at runtime. None of the major providers (OpenAI, Anthropic, xAI) offer a public API endpoint that returns per-model pricing rates. Verify rates at each provider's official pricing page and update `pricing.json` accordingly. AllNew LLC assumes no liability for inaccuracies in the pricing table.

When providers change their pricing, edit `~/.config/banto/pricing.json`. To add a new model, add an entry to both `providers` in `config.json` (for key resolution) and `pricing.json` (for cost calculation).

Three pricing types are supported:

```json
{
  "gpt-4o": {
    "type": "per_token",
    "input_per_1k": 0.0025,
    "output_per_1k": 0.01
  },
  "dall-e-3": {
    "type": "per_image",
    "variants": {
      "standard_1024x1024": 0.040,
      "hd_1024x1024": 0.080
    },
    "fallback": 0.120
  },
  "sora-2": {
    "type": "per_second",
    "rate": 0.10
  }
}
```

## How it works

### Hold/settle pattern

banto uses a **pessimistic reservation** (hold/settle) pattern for budget enforcement:

1. **Hold**: `get_key()` estimates the cost and writes a hold entry to the usage log *before* returning the key. The held amount counts against the budget immediately.
2. **Settle**: `record_usage()` finds the matching hold and replaces it with the actual cost. If actual < estimated, the surplus budget is freed.
3. **Safe-side bias**: If `record_usage()` is never called (crash, timeout, bug), the hold stays. The hold pattern is designed to prevent silent budget leakage.

This closes the metering gap where an agent could call `get_key()` but skip `record_usage()`, consuming API resources without being tracked.

### Multi-layer budget enforcement

Three layers are checked on every `get_key()` call (all must pass):

1. **Global limit**: Total monthly spend across all providers/models
2. **Provider limit**: Per-provider cap (e.g., OpenAI max $30/month)
3. **Model limit**: Per-model cap (e.g., DALL-E 3 max $10/month)

### Budget tracking

- Usage is logged per-call in `~/.config/banto/data/usage_YYYY_MM.json`
- Budget resets automatically each month (new file per month)
- Totals are recalculated from entries on every load (prevents drift)
- File locking (`fcntl`) provides process-safe concurrent access via exclusive-lock read-modify-write

### Keychain storage

- Keys are stored as generic passwords in the login keychain
- Service name format: `banto-<provider>` (e.g., `banto-openai`)
- Uses the macOS `security` CLI tool (no native bindings needed); account name resolved via `os.getlogin()`
- Keys are never written to disk -- no `.env` files, no config files
- Note: During `banto store`, the key is passed as a command-line argument to the `security` tool and is briefly visible in the process table. This is a limitation of the macOS `security` CLI.

### Budget-gated get_key()

`get_key()` is the central mechanism. It performs three operations in sequence:

1. Check if estimated cost fits within remaining budget (global + provider + model)
2. Write a hold entry reserving that cost in the usage log
3. Retrieve the API key from Keychain

If step 1 fails, steps 2-3 never execute. When over budget, the key is inaccessible through banto's API. An LLM agent that uses only banto's `get_key()` has no code path to obtain the key.

> **Threat model note**: banto protects against agents that access keys exclusively through `get_key()`. An agent with direct shell access could query macOS Keychain independently. For defense-in-depth, restrict shell access in your agent runtime.

## Custom backends

The secret storage is pluggable via the `SecretBackend` protocol. Any object with `get`, `store`, `delete`, `exists`, and `list_providers` methods works. No inheritance required.

### Environment variables

```python
import os
from banto import SecureVault

class EnvVarBackend:
    """Read API keys from BANTO_KEY_<PROVIDER> environment variables."""

    def get(self, provider: str) -> str | None:
        return os.environ.get(f"BANTO_KEY_{provider.upper()}")

    def store(self, provider: str, api_key: str) -> bool:
        os.environ[f"BANTO_KEY_{provider.upper()}"] = api_key
        return True

    def delete(self, provider: str) -> bool:
        return os.environ.pop(f"BANTO_KEY_{provider.upper()}", None) is not None

    def exists(self, provider: str) -> bool:
        return f"BANTO_KEY_{provider.upper()}" in os.environ

    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]

vault = SecureVault(caller="my_app", backend=EnvVarBackend())
```

### 1Password CLI

```python
import json
import subprocess
from banto import SecureVault

class OnePasswordBackend:
    """Retrieve API keys from 1Password using the `op` CLI."""

    def __init__(self, vault_name: str = "Private"):
        self.vault_name = vault_name

    def get(self, provider: str) -> str | None:
        try:
            result = subprocess.run(
                ["op", "item", "get", f"banto-{provider}",
                 "--vault", self.vault_name,
                 "--fields", "label=credential", "--format", "json"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return json.loads(result.stdout).get("value")
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    def store(self, provider: str, api_key: str) -> bool: ...
    def delete(self, provider: str) -> bool: ...
    def exists(self, provider: str) -> bool:
        return self.get(provider) is not None
    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]

vault = SecureVault(caller="my_app", backend=OnePasswordBackend())
```

### In-memory (for testing)

```python
from banto import SecureVault

class InMemoryBackend:
    def __init__(self, keys: dict[str, str] | None = None):
        self._store = dict(keys) if keys else {}

    def get(self, provider: str) -> str | None:
        return self._store.get(provider)
    def store(self, provider: str, api_key: str) -> bool:
        self._store[provider] = api_key
        return True
    def delete(self, provider: str) -> bool:
        return self._store.pop(provider, None) is not None
    def exists(self, provider: str) -> bool:
        return provider in self._store
    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if p in self._store]

vault = SecureVault(
    caller="test",
    backend=InMemoryBackend({"openai": "test-key-12345"}),
)
```

See [examples/06_custom_backend.py](./examples/06_custom_backend.py) for complete implementations.

## Advanced

### Custom Keychain prefix

If you already have keys stored under a different prefix:

```python
vault = SecureVault(
    caller="my_app",
    keychain_prefix="claude-mcp",  # uses "claude-mcp-openai" etc.
)
```

### Custom data directory

```python
vault = SecureVault(
    caller="my_app",
    data_dir="/path/to/usage/data",
)
```

### Explicit provider

When the model isn't in the config's provider mapping:

```python
key = vault.get_key(
    model="my-custom-model",
    provider="openai",
    input_tokens=1000,
    output_tokens=500,
)
```

### Using CostGuard directly (without secret storage)

For budget tracking only, with hold/settle:

```python
from banto import CostGuard, BudgetExceededError

guard = CostGuard(caller="my_mcp")

# Hold budget (reserves estimated cost)
hold_id = guard.hold_budget(model="dall-e-3", provider="openai",
                            n=1, quality="standard", size="1024x1024")
# ... call API ...

# Settle with actual cost
guard.settle_hold(hold_id, model="dall-e-3", n=1, provider="openai", operation="image")
```

Or use check + record without holds (backward compatible):

```python
guard.check_budget(model="dall-e-3", n=1, quality="standard", size="1024x1024")
# ... call API ...
guard.record_usage(model="dall-e-3", n=1, provider="openai", operation="image")
```

## Disclaimer

banto is a budget management aid, not a guarantee against excessive API charges. The authors shall not be liable for any financial losses arising from inaccurate pricing tables, software defects, configuration errors, or agents that bypass banto's API. Users are solely responsible for monitoring actual API spend through each provider's billing dashboard and for keeping the pricing table up to date.

See [LICENSE](./LICENSE) for full terms.

## License

Dual license:

- **Personal use** (free): Individuals may use, modify, and redistribute banto at no cost for personal, educational, and research purposes.
- **Commercial use** (paid): Organizations and companies require a commercial license from [AllNew LLC](https://github.com/allnew-llc/banto/issues).

See [LICENSE](./LICENSE) for full terms.

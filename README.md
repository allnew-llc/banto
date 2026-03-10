[日本語](./README.ja.md)

# banto

Structurally prevents excessive API charges caused by unexpectedly running LLM agents. A budget-gated API key vault for macOS.

> Named after the **bantō** (番頭) — the head clerk of Edo-period Japanese merchant houses who held the keys to the storehouse and managed the account books.

## Problem

LLM agents can exceed API budgets unchecked. Traditional budget checks are advisory -- the agent can catch the exception and call the API regardless, because the API key is already available in the environment. When agents run unexpectedly, this can result in significant charges.

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

### 2. Store API keys

```bash
banto store openai
banto store google
banto store anthropic
```

### 3. Use in your code

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

Default config is at `~/.config/banto/config.json`. Run `banto init` to create it.

### Budget limit

```json
{
  "monthly_limit_usd": 50.00
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

### Pricing

Three pricing types:

```json
{
  "pricing": {
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
      "rate": 0.15
    }
  }
}
```

## How it works

### Hold/settle pattern

banto uses a **pessimistic reservation** (hold/settle) pattern for budget enforcement:

1. **Hold**: `get_key()` estimates the cost and writes a hold entry to the usage log *before* returning the key. The held amount counts against the budget immediately.
2. **Settle**: `record_usage()` finds the matching hold and replaces it with the actual cost. If actual < estimated, the surplus budget is freed.
3. **Safe-side bias**: If `record_usage()` is never called (crash, timeout, bug), the hold stays. Budget is never silently leaked.

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
- File locking (`fcntl`) ensures process-safe concurrent access

### Keychain storage

- Keys are stored as generic passwords in the login keychain
- Service name format: `banto-<provider>` (e.g., `banto-openai`)
- Uses the macOS `security` CLI tool (no native bindings needed)
- Keys are never written to disk -- no `.env` files, no config files

### Atomic get_key()

`get_key()` is the core innovation. It combines three operations into one:

1. Check if estimated cost fits within remaining budget (global + provider + model)
2. Write a hold entry reserving that cost in the usage log
3. Retrieve the API key from Keychain

If step 1 fails, steps 2-3 never execute. The key is inaccessible through banto's API when over budget. An LLM agent using banto's `get_key()` cannot bypass this -- the key is never returned.

> **Threat model note**: banto protects against agents that access keys exclusively through `get_key()`. An agent with direct shell access could query macOS Keychain independently. For defense-in-depth, restrict shell access in your agent runtime.

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

### Using CostGuard directly (without Keychain)

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

## License

Apache 2.0

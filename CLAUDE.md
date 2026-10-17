# CLAUDE.md — banto Agent Instructions

## What banto is

banto is a local-first secret management tool. API keys are stored in macOS Keychain
and can be synced to 33 cloud platforms. Optional modules: budget gating (LLM cost control)
and dynamic leases (short-lived credentials with TTL).

## Critical Rule: NEVER read secret values

**You must NEVER read, display, log, or request the actual value of any API key or secret.**

- Do NOT run `security find-generic-password -w` or any command that outputs key values
- Do NOT read `.env` files that contain secret values
- Do NOT ask the user to paste API keys into the chat
- Use `banto register` or the MCP `banto_register_key` tool to let the user enter keys via browser

## MCP Server

banto exposes an MCP server for native tool integration. Register in `.mcp.json`:

```json
{
  "mcpServers": {
    "banto": {
      "command": "banto-mcp",
      "args": []
    }
  }
}
```

### Available MCP tools

14 tools; in broker mode (`banto broker enable`) the server also exposes
`banto_api_request` and `banto_broker_health` (16 total).

| Tool | Purpose | Safe |
|------|---------|------|
| `banto_sync_status` | Show sync matrix (secrets × targets) | Always safe |
| `banto_sync_push` | Push secrets to cloud targets | Modifies cloud state |
| `banto_sync_audit` | Check drift and staleness | Always safe |
| `banto_validate` | Validate keys in sync.json | Sends keys to provider APIs |
| `banto_validate_keychain` | Scan + validate Keychain keys | Sends keys to provider APIs |
| `banto_budget_status` | Budget breakdown | Always safe |
| `banto_register_key` | Open browser for key entry | Opens browser, human enters key |
| `banto_lease_list` | List active leases | Always safe |
| `banto_lease_cleanup` | Revoke expired leases, retry failed revocations | Runs revoke commands, modifies Keychain |
| `banto_sync_setup` | Discover platform env vars, match Keychain | Queries cloud platforms |
| `banto_sealed_create_key` | Create a Secure Enclave signing key | Private key never leaves the enclave |
| `banto_sealed_export_pubkey` | Export a sealed public key | Always safe |
| `banto_sealed_sign` | Sign a payload with a sealed key | May prompt Touch ID / passcode |
| `banto_sealed_list` | List sealed keys | Always safe |
| `banto_api_request` | Broker mode: OpenAI Responses / Anthropic Messages call without the key | Costs money; budgeted, rate limited, audited |
| `banto_broker_health` | Broker mode: liveness check | Always safe |

### When to use each tool

- User says "deploy my secrets" → `banto_sync_push`
- User says "check if my keys work" → `banto_validate_keychain`
- User says "add a new API key" → `banto_register_key` (opens browser)
- User says "are my secrets in sync" → `banto_sync_audit`
- User says "what's my budget" → `banto_budget_status`
- Before making API calls → `banto_validate` to check key validity
- Need an LLM call with the user's key (broker mode) → `banto_api_request` with an explicit
  model and `max_output_tokens` / `max_tokens`; never ask for the key itself

## CLI Commands (when MCP is not available)

```bash
banto sync status          # sync matrix
banto sync push            # push to targets
banto sync audit           # drift check
banto sync validate --keychain  # validate Keychain keys
banto register [provider]  # browser popup for key entry
banto status               # budget status
banto lease list           # active leases
```

## Prohibited operations

- `banto sync export` outputs secret VALUES — do not run or suggest
- `banto sync run` injects secrets into env — do not run in agent context
- `banto lease get` prints a credential VALUE — do not run or suggest
- Do not read `~/.config/banto/sync.json` (metadata only, but respect boundaries)
- Do not directly access Keychain via `security` CLI

## Common workflows

### User needs to add a new API key
```
1. Use banto_register_key(provider="openai")
2. Tell user: "I've opened a browser window for you to enter your OpenAI API key"
3. After user confirms, use banto_validate to verify
4. Use banto_sync_push to deploy to targets
```

### User wants to check secret health
```
1. banto_validate_keychain  → shows PASS/FAIL/UNKNOWN per key
2. banto_sync_audit         → shows drift across targets
```

### User wants to rotate a key
```
1. banto_register_key(provider="openai")  → user enters new key
2. banto_sync_push(name="openai")         → push to all targets
```

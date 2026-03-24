# Changelog

## 5.0.0 (2026-03-24) — Agent-Native Secret Management

### Architecture: Agent + Human collaboration model

banto is now designed for AI coding agents (Claude Code, Codex) to orchestrate
secret management while humans provide the actual secret values via browser.

```
Agent (Claude/Codex)              Human
  │                                 │
  ├─ banto_sync_status             │
  ├─ banto_validate_keychain       │
  ├─ banto_register_key ──────────▶ Browser popup
  │                                 │ enters API key
  ├─ banto_sync_push               │
  └─ (never sees secret values)    └─ (only source of values)
```

### New: MCP Server for Claude Code

- `banto-mcp` entry point — register in `.mcp.json` for native tool access
- 9 tools: sync_status, sync_push, sync_audit, validate, validate_keychain,
  budget_status, register_key, lease_list, lease_cleanup
- Agent NEVER receives secret values — all tools return metadata only
- `banto_register_key` opens a browser popup for human key entry

### New: Browser registration popup

- `banto register [provider]` — opens minimal, focused browser popup
- Provider presets with auto-filled env var names
- Password input with show/hide, stores directly to Keychain
- Single-use: server stops after successful registration
- Localhost only (127.0.0.1), value never echoed back

### New: CLAUDE.md agent instructions

- Defines safe/prohibited operations for AI agents
- MCP tool usage guide with common workflows
- Critical rule: agents must NEVER read secret values

### New: OpenAI Apps SDK compatibility

- MCP server supports 3 transports: `stdio` (Claude Code), `sse` (Apps SDK dev), `http` (production)
  - `banto-mcp --transport sse --port 8385`
  - `banto-mcp --transport http --port 8385`
- All 9 tools have OpenAI-required annotations: `readOnlyHint`, `destructiveHint`, `openWorldHint`
- Response format compatible with `structuredContent` + `content` model

### New: --json flag for all CLI commands

- Every command supports `--json` for machine-readable output
- Enables AI agents to parse results without screen-scraping

### Breaking Changes

- Major version bump (4.x → 5.0) due to new agent-native architecture
- `banto-mcp` requires optional `mcp` dependency: `pip install banto[mcp]`

## 4.2.0 (2026-03-24) — Security Audit Response

Addresses all findings from Codex security re-audit (2026-03-24).

### Security (Blocker + High + Medium fixes)

- **[Blocker] All 33 sync drivers**: secret values removed from subprocess argv. Uses stdin pipe (14 drivers), tempfile 0o600 (8 drivers), or curl `-K -`/`-d @file` (8 drivers). 6 drivers were already safe
- **[High] KeychainStore rewritten with ctypes**: `store()` and `get()` now call macOS Security framework directly (SecKeychainAddGenericPassword / SecKeychainFindGenericPassword). Secret values never enter process arguments, temp files, or shell expansions
- **[High] README/CHANGELOG corrected**: removed misleading "argv fixed" and "not in env vars" claims. Documented that `sync run`/`export`/custom backends intentionally materialize secrets
- **[Medium] History account naming fixed**: `name:v1` → `name--v1` to avoid provider validator colon rejection
- **[Medium] validate made opt-in**: Keychain scan requires explicit `--keychain` flag. Added `--dry-run`. No longer silently sends keys to provider endpoints
- **[Medium] lease revoke argv exposure fixed**: credential passed via `BANTO_LEASE_VALUE` env var instead of `{value}` expansion into argv

## 4.1.0 (2026-03-24)

- **New: `banto sync validate`** — Test API keys against provider endpoints before pushing. Supported: OpenAI, Anthropic, Gemini, GitHub, Cloudflare, xAI. Read-only health checks, no data modified. Pattern matching for Keychain service names (e.g. `claude-mcp-openai` → openai validator)
- **New: `banto sync push --validate`** — Pre-push validation gate. Blocks push if any key is invalid. Prevents propagating broken keys to cloud targets
- **New: fingerprint-based drift detection** — `banto sync push` now records SHA-256 fingerprint of each pushed value. `banto sync audit` compares current Keychain fingerprint against last-pushed fingerprint to detect local changes not yet synced
- **Enhanced: `banto sync audit`** — Now checks 4 dimensions:
  1. Existence drift (missing in Keychain or target)
  2. Fingerprint drift (Keychain changed since last push)
  3. Local file value mismatch (actual `.env` content vs Keychain)
  4. Rotation staleness (`--max-age-days N`)

## 4.0.0 (2026-03-24)

### Architecture: Modular banto

banto is now a **modular secret management platform**. Budget gating and dynamic leases are opt-in — the core is pure key storage + multi-platform sync.

```
banto/
├── core     : Keychain + sync (33 platforms)  ← everyone
├── budget   : LLM cost gating (hold/settle)   ← opt-in
└── lease    : dynamic secrets with TTL         ← opt-in
```

### New: Optional budget mode

- `SecureVault(budget=False)` — keys returned directly, no cost checks (NEW DEFAULT)
- `SecureVault(budget=True)` — existing hold/settle behavior preserved
- `SecureVault()` (budget=None) — auto-detects from config: enabled when `monthly_limit_usd > 0`
- `budget_enabled` property for runtime checking
- `record_usage()`, `get_budget_status()`, `estimate_cost()` gracefully no-op when budget disabled
- `set_budget()` lazily initializes the budget subsystem

### New: `banto lease` — dynamic secrets with TTL

- `banto lease acquire <name> --cmd '<generate>' [--revoke-cmd '<revoke>'] [--ttl 3600]`
  - Generate short-lived credentials via external commands
  - Store temporarily in Keychain, auto-revoke on TTL expiry
  - `{value}` and `{lease_id}` placeholders in revoke commands
- `banto lease get <lease_id>` — retrieve credential (stdout, for piping)
- `banto lease revoke <lease_id>` — explicit revocation
- `banto lease list` — show active leases with remaining TTL
- `banto lease cleanup` — revoke all expired leases
- Lease state tracked in `~/.config/banto/lease-state.json` (no values stored — values stay in Keychain)

### Breaking Changes

- `SecureVault` default behavior changed: `budget=None` (auto-detect) instead of always-on budget
- Users with `monthly_limit_usd > 0` in config.json: **no change** (auto-detected as budget=True)
- Users without config.json: `get_key(provider="openai")` now works without budget setup
- Description changed: "Budget-gated API key vault" → "Local-first secret management"

## 3.1.0 (2026-03-24)

- **New: `banto sync rotate`** — Rotate a secret interactively or via `--from-cli '<command>'`. Updates Keychain, records version history, re-syncs all targets
- **New: `banto sync run`** — Inject secrets as environment variables and run a command (`banto sync run [--env prd] -- <cmd>`). Supports environment inheritance
- **New: `banto sync import`** — Import secrets from `.env` or `.json` files. Auto-detect format, store in Keychain, add to config, record history. Skips duplicates
- **New: `banto sync audit --max-age-days N`** — Flags secrets not rotated within threshold. Uses version history timestamps. Reports "STALE" alongside existing drift detection

## 3.0.0 (2026-03-24)

- **New: `banto sync` subpackage** — Multi-platform secret sync engine (ported from andon-for-llm-agents vault module)
  - `banto sync init` — Create sync.json config
  - `banto sync status` — Sync status matrix across all targets
  - `banto sync push [name]` — Distribute secrets from Keychain to platforms
  - `banto sync add` — Add new secret with target mappings
  - `banto sync audit` — Drift detection across all targets
  - `banto sync history <name>` — Version history with fingerprints
  - `banto sync export` — Export in env/json/docker formats
  - `banto sync ui` — Localhost-only web dashboard (stdlib only)
- **New: 33 platform drivers** — Cloudflare Pages, Vercel, AWS SM/SSM, GCP, Azure Key Vault, Kubernetes, Docker Swarm, Heroku, Fly.io, Netlify, Render, Railway, Supabase, GitLab CI, GitHub Actions, CircleCI, Bitbucket, Terraform Cloud, Azure DevOps, Deno Deploy, Hasura Cloud, Laravel Forge, DigitalOcean, Alibaba KMS, Tencent SSM, Huawei CSMS, Naver Cloud, NHN Cloud, JD Cloud, Sakura Cloud, Volcengine KMS
- **New: 4 notification integrations** — Slack, Microsoft Teams, Datadog Events, PagerDuty
- **New: environment inheritance** — dev/stg/prd with chain resolution (child overrides parent, base always included)
- **New: Keychain-native version history** — Rollback values stored in Keychain (not in JSON file). SHA-256 fingerprints, max 50 versions
- **New: sync audit logging** — All operations logged to `~/.config/banto/sync-audit.log` (values never logged)
- **New: sync config** — JSON-based `~/.config/banto/sync.json` (metadata only, no secret values)

### Security

- **Fixed: KeychainStore — ctypes Security framework** — `store()` and `get()` now use macOS Security framework directly via ctypes (SecKeychainAddGenericPassword / SecKeychainFindGenericPassword). Secret values never appear in process arguments, temp files, or shell expansions
- **Fixed: all 33 sync drivers** — secret values no longer passed as subprocess argv. Uses stdin pipe, tempfile (0600), or env var depending on driver CLI capabilities
- **Fixed: lease revoke** — credential value passed via `BANTO_LEASE_VALUE` env var instead of expanding into argv
- **Fixed: sync validate** — Keychain scan now requires explicit `--keychain` flag (no longer defaults to sending keys). Added `--dry-run` mode
- **Fixed: history account naming** — version accounts use `name--v1` format (was `name:v1` which conflicted with provider name validator)
- **Keychain-native history** — Version rollback values stored directly in macOS Keychain. JSON history file contains only metadata (timestamp + fingerprint)
- Zero new dependencies (still stdlib-only, ctypes is part of stdlib)

### Breaking Changes

- `KeychainStore.store()` internal implementation changed (tmpfile instead of direct argv). Public API unchanged
- Version bump to 3.0.0 due to scope expansion (sync subpackage adds ~4,300 LOC)

## 2.3.0 (2026-03-10)

- **New: budget-based profile recommendation**: `CostGuard.recommend_profile()` returns "quality" (>50% remaining), "balanced" (>20%), or "budget" (<=20%). Displayed in `banto status` output
- **New: hold timeout (stale hold cleanup)**: Holds older than `hold_timeout_hours` (default 24h) are automatically voided during budget operations. Voided entries preserved for audit trail with `status: "voided_timeout"`. Configurable via `config.json`
- **New: model profile configuration**: Named profiles (quality/balanced/budget) map task roles (chat/verify/embed) to specific models. `vault.get_key(role="chat")` resolves through active profile. Direct `model=` specification takes priority
- **New: `ProfileManager` class** in `banto/profiles.py`: manages profile definitions, active profile switching, and role-to-model resolution
- **New: `banto profile` CLI command**: view all profiles or switch active profile
- **New: `banto status` enhancements**: shows recommended profile and stale hold summary
- Backward compatible: all existing APIs unchanged

### Security

- **Process-safe `set_budget()`**: Budget limit updates wrapped in `fcntl` exclusive lock for entire read-modify-write cycle
- **Copyright headers**: All source files include copyright and license headers
- **Service prefix validation**: `KeychainStore` validates `service_prefix` against injection patterns
- **Negative parameter validation**: `_lookup_price()` rejects negative token/image/second counts
- **CLI bounds validation**: All CLI numeric inputs validated within safe ranges (tokens 0-100M, budget 0-1M)
- **UTC datetime consistency**: All internal timestamps use `datetime.now(timezone.utc)`
- **Error message hardening**: Model lists removed from budget error messages to prevent information leakage
- **Hold settlement safety**: `settle_hold()` raises `ValueError` on unknown hold_id instead of silent pass
- **Float precision defense**: `round(total_usd, 10)` prevents floating-point accumulation drift
- **Entry count warning**: Usage log warns at >10,000 entries to prevent unbounded growth
- **Keychain security documentation**: Security limitations of `security` CLI subprocess documented in code
- **Accurate documentation**: "atomic" claims replaced with precise "exclusive-lock read-modify-write" descriptions

## 2.2.0 (2026-03-10)

- **Separate pricing file**: Pricing table moved from `config.json` to dedicated `pricing.json` for independent updates. Backward compatible (inline `"pricing"` key still supported)
- **Fix: process-safe concurrent access**: Usage file read-modify-write now holds exclusive `fcntl` lock for the entire cycle, preventing data loss between concurrent processes
- **Fix: hold entry param typo**: `max_output_tokens` → `output_tokens` in hold entries
- **Fix: metadata key guard**: `_notice` and other `_`-prefixed keys in pricing.json now correctly rejected as model names
- **Fix: concurrent hold tracking**: Multiple `get_key()` calls for the same model no longer overwrite each other's hold IDs (FIFO queue)
- **Fix: hold rollback on key retrieval failure**: `get_key()` now voids the budget hold if the backend fails to return a key, preventing permanent budget leakage
- **New: `void_hold()`**: Explicitly cancel a budget hold to free reserved budget
- **New: negative budget validation**: `set_budget()` rejects negative limit values
- **Security: `exists()` no longer retrieves full key**: Keychain existence check uses metadata-only query
- **Security: replaced `whoami` subprocess** with `os.getlogin()` in KeychainStore

## 2.1.0 (2026-03-10)

- **Pluggable secret backends**: `SecretBackend` protocol allows swapping macOS Keychain for 1Password, environment variables, or any custom store via `SecureVault(backend=...)`
- New export: `SecretBackend` protocol class
- New example: `examples/06_custom_backend.py` (EnvVar, 1Password, InMemory)
- Backward compatible: existing code using `keychain_prefix=` continues to work

## 2.0.0 (2026-03-10)

Initial public release.

- **SecureVault**: Budget-gated API key access combining macOS Keychain + monthly budget enforcement
- **CostGuard**: Standalone budget tracker with three pricing models (per_token, per_image, per_second)
- **KeychainStore**: macOS Keychain wrapper using `security` CLI
- **CLI**: `banto status|budget|store|delete|list|check|init`
- **Hold/settle pattern**: Pessimistic budget reservation at `get_key()` time, settled to actual cost at `record_usage()`. Unsettled holds stay reserved (safe-side bias)
- **Multi-layer budget enforcement**: Global limit + per-provider limits + per-model limits (all must pass)
- **Budget CLI**: `banto budget` to view/set global, provider, and model limits
- Process-safe concurrent access via `fcntl.flock()` + `threading.Lock()`
- Monthly auto-reset (usage files per month)
- Provider-to-model mapping for automatic key resolution
- **Dual license**: Free for individuals, commercial license required for organizations

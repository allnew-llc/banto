# Changelog

## 5.3.0 (2026-10-17)

### Added

- **Budgeted broker `api_request`**: with budget gating on, the broker no longer rejects generation calls. It requires `max_output_tokens` (OpenAI) / `max_tokens` (Anthropic), rejects models without a `per_token` price (`unpriced_model`), reserves the worst case as a durable hold before the call (`budget_exceeded` if it does not fit global/provider/model limits), and settles from the provider's reported usage (Anthropic cache tokens included). A 4xx releases the hold; network errors, timeouts and 5xx keep it (`upstream_failed_or_outcome_unknown`). Responses carry `banto_budget`
- **Broker rate limit**: `broker.api_request_rpm` in `config.json` (default 30/min, `0` disables); over the limit returns `rate_limited; retry after Ns`
- **Broker audit log**: every operation except `health` is appended to `audit.jsonl` (0600) in the broker runtime directory with metadata only (operation, result, error code, allow-listed provider/model/cost/token fields, argument names) and a SHA-256 hash chain that survives rotation. `banto broker audit [--tail N] [--verify]`
- **Pricing freshness**: `pricing.json` has machine-readable `_updated` and per-entry `verified` dates; `banto status` (text and `--json` `pricing`) warns about entries older than 90 days and about profile models without a price
- **Prices** (official pages, 2026-10-17): `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` (the default profile models), `text-embedding-3-small`, `text-embedding-3-large`. OpenAI GPT-4.1/4o/o-series and `claude-sonnet-4-20250514` re-verified (unchanged). Image/video entries were not re-verified and keep their 2026-03-10 date
- **Release automation**: `.github/workflows/release.yml` (tag `v*`: version check, tests, `uv build`, `twine check`, CycloneDX SBOM, build-provenance attestation, PyPI Trusted Publishing with PEP 740 attestations when the `PYPI_TRUSTED_PUBLISHING` repository variable is `true`, GitHub Release with artifacts)
- CI: Python 3.14; all GitHub Actions pinned by commit SHA; Dependabot for Actions and uv
- Drift tests: README/CLAUDE.md MCP tool tables vs `MCP_OPERATIONS`, README sync tables vs `SYNC_COMMANDS`, version consistency (pyproject, `__version__`, uv.lock, CHANGELOG)

### Fixed

- **Leases**: a failed revoke command no longer deletes the Keychain value or marks the lease revoked; it is recorded as `revoke_failed` (with error, attempt count and time), `lease revoke`/`lease cleanup` exit 1, and `cleanup` retries it. `$BANTO_LEASE_VALUE` in `--revoke-cmd` now works (run via `/bin/sh -c` with the value in the environment, never argv). Lease state writes are atomic and serialized with a lock file; `lease list` has no side effects and shows pending revocations; TTL must be 1 s – 7 days. MCP `banto_lease_cleanup` declares `openWorldHint: true` and reports `revoke_failed`
- **`banto list`** read the wrong Keychain namespace (`banto` instead of the sync `keychain_service` used by `store`/`register`) and listed nothing when budgets were off
- **`banto profile`** crashed when budgets were off and did not persist the selection; it now works without budgets and saves `active_profile` to `~/.config/banto/config.json`
- **`banto budget` / `CostGuard.set_budget()`** could write into the installed package's `config.json`; the first write now creates the user config instead
- **`banto check`** reported `BLOCKED` when budgets were off and ignored provider/model limits; it now reports `ALLOWED (budget disabled)` or applies all three limits (reporting the blocking scope), and exits 1 for unknown models
- **`banto register-x-oauth`** (broker mode) routed to a popup that did not exist; the command now points to `banto sync x-oauth-authorize` and exits 2, and the broker rejects the operation

### Removed

- Client-specific code and documents that do not belong in the public package (provider-specific broker operations, validators, credential wrappers, rotation rules and an internal rotation run log) were removed. Generic browser-recipe issuance (`banto sync browser-issue`) covers the same flows. Note: these files remain in git history and in the 5.2.x sdists/wheels on PyPI

### Documentation

- README (EN/JA): local broker section (install from PyPI, broker-mode command restrictions, budgeted `api_request`, rate limit, audit log), 14 MCP tools / 16 in broker mode, corrected sync command table (31 commands incl. `x-oauth-authorize`, `x-oauth-user-token`), `register-asc`, `sealed`, `broker` commands, lease behaviour, CI badge instead of a static test count, removed the "no daemon" claim
- CLAUDE.md: full tool table (14 + 2 broker tools); `banto lease get` prohibited for agents
- docs/local-broker.md: PyPI install path, budget hold/settle behaviour, rate limit, audit log

### Backfilled (shipped in 5.2.x without a CHANGELOG entry)

- **Local broker** (`banto broker install|health|enable|disable|call`, `banto.broker_client.BrokerClient`, `banto-mcp` forwarding, `python -m banto.broker_setup` for Codex/Claude Code/Gemini CLI): one per-user LaunchAgent owns Keychain access over a peer-UID-checked Unix socket; broker mode makes direct Keychain paths fail closed; `api_request` for OpenAI Responses / Anthropic Messages without exposing keys
- **Secure Enclave sealed signing**: `banto sealed create-key|export-pubkey|sign|verify|list|delete` and the four `banto_sealed_*` MCP tools (P-256, private keys never leave the enclave)
- **X OAuth 2.0**: `banto sync x-oauth-authorize` (PKCE enrolment with exact scopes) and `banto sync x-oauth-user-token` (refresh-token rotation)
- **App Store Connect**: `banto register-asc` popup for `.p8` API keys
- **Browser-recipe issuance**: `banto sync browser-record|browser-issue|browser-revoke|browser-batch`, plus `import-keychain`, `cloudflare-account-token`, `stripe-webhook-endpoint`, `xai-api-key`, OpenAI service-account commands
- Durable budget holds (`hold_budget(..., durable=True)`) that are not voided by `hold_timeout_hours`

## 5.2.4 (2026-10-17)

### Added

- **`banto --version` / `banto -V` / `banto version`**: print `banto <version>` and exit 0. Handled before broker routing, so it also works in broker mode (it never touches Keychain, the broker socket, or config). Listed in `banto --help` and the README core-command tables (EN/JA). Tests added

### Changed

- CHANGELOG: corrected the "5.2.2 additional changes" entry that still said banto is not published on PyPI

## 5.2.3 (2026-10-17)

### Fixed

- **Python 3.10 compatibility**: fall back to `tomli` when `tomllib` (3.11+) is unavailable; `tomli` is a dependency only for `python_version < "3.11"`
- **Broker client**: could misreport `peer_denied` as `broker_unavailable` when the broker closed the connection early (the client now reads the broker's reply after a broken pipe on send). Regression test added

## 5.2.2 additional changes (included in the 5.2.2 PyPI release, tag v5.2.2)

### Security

- **DNS rebinding defense**: the local web UIs (`banto sync ui` on fixed port 8384 and the registration popup) now reject requests whose `Host` header is not `127.0.0.1:<port>` / `localhost:<port>`. CSRF tokens are compared with `hmac.compare_digest`
- **Removed committed `.wrangler/` cache** (contained a Cloudflare account ID and account e-mail) and added `.wrangler/` to `.gitignore`. Note: the files remain in git history

### Changed

- README: install instructions pointed at the GitHub repository (`uv tool install "git+https://github.com/allnew-llc/banto.git"`) because banto was not yet on PyPI when this entry was written. 5.2.2 was then published to PyPI (2026-10-17), and the README now installs from PyPI (`pip install banto`), keeping `git+https` for the latest development version (#19)
- README: version/test/MCP-tool badges and tool table synced with the code (5.2.2, 14 MCP tools incl. sealed signing)
- MCP annotations: `banto_sync_push` and `banto_sync_setup` now declare `openWorldHint: true` (they talk to external cloud platforms)
- `banto.__version__` synced to 5.2.2 (matches `pyproject.toml`)


### Fixed

- **Vercel push silently not updating values** (2026-08-09 incident): `vercel env add` in non-interactive mode with `--sensitive` reports success (exit 0) while failing to persist the value — old value retained on `--force` upserts of existing rows, empty value on fresh adds (vercel/vercel#16160). The driver now uses the proven-working form `vercel env add <NAME> <environment> --force` with the value on stdin, and no longer passes `--sensitive`/`--yes` to `env add`. Values remain encrypted at rest; enforce the sensitive marking via Vercel's team-level sensitive environment variable policy
- **Preview pushes**: no longer pass an empty git-branch positional; `vercel env add <NAME> preview` applies to all branches by default

### Added

- **Verified Vercel writes**: after `vercel link`, the driver checks `.vercel/project.json` `projectName` against the requested project and refuses to write on mismatch (fail closed — `vercel link --yes` can resolve to or auto-create an unintended project). After each `env add`, the row's existence is confirmed via `vercel env ls <environment>` before reporting OK
- **Team-scoped Vercel targets**: `--target vercel:<team>/<project>` passes `--scope <team>` to `vercel link`, so resolution no longer depends on the CLI's default team
- **`banto sync add --environments production,preview`**: sets `Target.environments` from the CLI (previously only editable by hand in sync.json), controlling which Vercel environment rows a push updates

## 5.2.2 (2026-03-27) — comprehensive audit fixes

- H-01: `validate_keychain` uses ctypes `_ctypes_get()` instead of `security -w` (no secret values in process args)
- M-01: registration popup returns 400 on malformed `Content-Length`
- M-02: lease revoke failures are recorded (`revoke_error`) instead of swallowed
- M-03: MCP HTTP/SSE transports are forced to `127.0.0.1`; warning when `BANTO_MCP_PATH_TOKEN` is unset
- M-05: `mcp` dependency pinned to `<2`
- L-01: `--port` parse errors handled

## 5.2.1 (2026-03-27) — registration popup fixes

- Registration popup / `banto register` now store keys under the `banto-sync` Keychain prefix used by sync tools
- Registration popup no longer triggers Safari/iCloud password autosave (`type="text"` + `-webkit-text-security`)
- `banto register <custom>` pre-fills the custom provider field; provider dropdown expanded to 19 entries

## 5.2.0 (2026-03-26) — sync setup Security Hardening

Addresses findings from independent security and legal re-audit (March 25, 2026).

### Security

- **Fail-closed discovery**: `sync setup` no longer silently falls back to the full env var catalog when platform discovery returns empty. Returns `discovery_empty` status and suggests `--guess` flag for explicit fallback
- **Project-scoped Vercel queries**: `_list_vercel_env_vars()` now passes `--project <name>` to scope queries to the requested project, preventing cross-project secret deployment risk
- **Excluded service policy shared**: `gh:github.com` removed from `ENV_TO_KEYCHAIN`. `_find_keychain_match()` now consults `EXCLUDED_SERVICES` and `EXCLUDED_PATTERNS` from `validate.py`, ensuring OAuth/session tokens cannot be auto-registered via setup

### Documentation

- **README (EN/JA)**: Updated `sync setup` descriptions to accurately reflect project-scoped platform queries. Added note about fail-closed behavior and `--guess` flag
- **Privacy Policy (EN/JA)**: Added `sync setup` section to the external-communication disclosure list. Documents that only env var names (metadata) are retrieved, not secret values

### Testing

- **17 new tests** for `setup.py`: coverage for `_is_excluded`, `_find_keychain_match`, `_list_vercel_env_vars`, `run_setup` (fail-closed, guess mode, scanner results, already-configured, unknown platform), and `ENV_TO_KEYCHAIN` ↔ `EXCLUDED_SERVICES` consistency

## 5.1.0 (2026-03-25) — Security Hardening + ChatGPT Connector

### Security

- **CSRF + Origin defense for web UI**: All POST endpoints now require a per-session CSRF token (`X-CSRF-Token` header), validate `Origin` header against localhost, and enforce `Content-Type: application/json`
- **Capability URL for ChatGPT connect**: `banto chatgpt connect` generates a random path token (`/mcp-{token}`). The URL is the bearer credential — no separate auth needed, but URL must be treated as secret
- **History fail-closed**: `record()` no longer saves metadata if Keychain write fails. Previously recorded metadata for broken versions (fail-open). Now returns `None` on failure
- **Register popup**: localhost-only, random port, single-use (already existed but now noted as reviewed in independent security assessment)

### New

- **`banto chatgpt connect`**: One-command setup for ChatGPT Connector — starts MCP HTTP server + ngrok/cloudflared tunnel, prints secure Connector URL
- **Widget templates** (`widgets.py`): 6 inline card templates for ChatGPT (sync status, validation, register prompt, budget, audit, leases)
- **Golden test prompts** (`docs/chatgpt-test-prompts.md`): 14 test cases for ChatGPT App testing
- **Privacy Policy** (`docs/privacy-policy.md`): EN/JA dual-language, covers tunnel provider disclosure
- **App icon** (`docs/assets/app-icon.png`)

### Note on ChatGPT App Store

banto is designed for use as a **local ChatGPT Connector** (Developer Mode), not for public App Store submission. OpenAI's App Store submission guidelines prohibit collecting API keys, which is banto's core function. The local Connector approach provides full functionality without this restriction.

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

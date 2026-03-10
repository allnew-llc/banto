# Changelog

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

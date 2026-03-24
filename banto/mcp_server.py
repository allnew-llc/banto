# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""MCP server for banto — exposes secret management tools to Claude Code.

Critical design principle: **agents NEVER see secret values**.
All tools return metadata, status, and results — never the actual API key values.

Launch via:
    python -m banto.mcp_server
    # or via MCP config with the `banto-mcp` entry point
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("banto", description="Local-first secret management — sync, validate, budget")


# ---------------------------------------------------------------------------
# Tool 1: banto_sync_status
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_sync_status() -> dict:
    """Show sync status matrix (secrets x targets, with existence status).

    Returns a list of secrets with Keychain existence and per-target status.
    No secret values are returned — only metadata.
    """
    from .sync.config import SyncConfig
    from .sync.sync import check_status

    config = SyncConfig.load()
    if not config.secrets:
        return {"secrets": [], "message": "No secrets configured in sync.json"}

    entries = check_status(config)
    result = []
    for entry in entries:
        result.append({
            "name": entry.secret_name,
            "env_name": entry.env_name,
            "keychain_exists": entry.keychain_exists,
            "targets": {label: status for label, status in entry.target_status.items()},
        })
    return {"secrets": result}


# ---------------------------------------------------------------------------
# Tool 2: banto_sync_push
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_sync_push(name: str = "") -> dict:
    """Push secrets from Keychain to cloud targets.

    Args:
        name: Optional specific secret name. If empty, pushes all secrets.

    Returns sync results with success/failure per target.
    No secret values are returned.
    """
    from .sync.config import SyncConfig
    from .sync.sync import sync_all, sync_secret

    config = SyncConfig.load()

    if name:
        report = sync_secret(config, name)
    else:
        report = sync_all(config)

    results = [
        {
            "name": r.secret_name,
            "target": r.target_label,
            "success": r.success,
            "message": r.message,
        }
        for r in report.results
    ]
    return {
        "ok": report.all_ok,
        "ok_count": report.ok_count,
        "fail_count": report.fail_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Tool 3: banto_sync_audit
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_sync_audit(max_age_days: int = 0) -> dict:
    """Check drift, fingerprint changes, and staleness of synced secrets.

    Args:
        max_age_days: If >0, flag secrets not rotated within this many days.

    Returns a list of issues found (drift, staleness, mismatches).
    No secret values are returned — only fingerprints and status.
    """
    from datetime import datetime, timezone

    from .keychain import KeychainStore
    from .sync.config import SyncConfig
    from .sync.history import HistoryStore
    from .sync.sync import check_status
    from .sync.sync_state import SyncState
    from .sync.sync_state import fingerprint as fp

    config = SyncConfig.load()
    kc = KeychainStore(service_prefix=config.keychain_service)
    entries = check_status(config)
    state = SyncState()
    issues: list[str] = []

    for entry in entries:
        name = entry.secret_name

        # Existence drift
        if not entry.keychain_exists:
            issues.append(f"DRIFT {entry.env_name}: missing in Keychain")
            continue
        for label, status in entry.target_status.items():
            if status is False:
                issues.append(f"DRIFT {entry.env_name} -> {label}")

        # Fingerprint drift
        secret_entry = config.get_secret(name)
        value = kc.get(secret_entry.account) if secret_entry else None
        if value:
            drift = state.check_drift(name, value)
            rec = state.get_push_record(name)
            if drift == "drift_local":
                pushed_at = rec.pushed_at[:10] if rec else "?"
                issues.append(
                    f"DRIFT {name}: Keychain changed since last push "
                    f"(current={fp(value)}, pushed={rec.fingerprint}, at={pushed_at})"
                )
            elif drift == "never_pushed":
                issues.append(f"DRIFT {name}: never pushed (no sync record)")

    # Rotation age check
    if max_age_days and max_age_days > 0:
        history = HistoryStore()
        now = datetime.now(timezone.utc)
        for name in config.secrets:
            versions = history.list_versions(name)
            if not versions:
                issues.append(f"STALE {name}: no version history")
                continue
            latest = versions[-1]
            try:
                ts = datetime.fromisoformat(latest.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).days
                if age_days > max_age_days:
                    issues.append(
                        f"STALE {name}: last rotated {age_days}d ago "
                        f"(threshold: {max_age_days}d)"
                    )
            except (ValueError, TypeError):
                issues.append(f"STALE {name}: unparseable timestamp in history")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Tool 4: banto_validate
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_validate() -> dict:
    """Validate API keys configured in sync.json against provider endpoints.

    Makes minimal read-only API calls to check if keys are valid.
    No secret values are returned — only pass/fail/unknown status.
    """
    from .keychain import KeychainStore
    from .sync.config import SyncConfig
    from .sync.validate import validate_key

    config = SyncConfig.load()
    if not config.secrets:
        return {"results": [], "message": "No secrets in sync.json"}

    kc = KeychainStore(service_prefix=config.keychain_service)
    results = []
    for name, entry in config.secrets.items():
        value = kc.get(entry.account)
        if not value:
            results.append({
                "name": name,
                "provider": name,
                "status": "unknown",
                "message": "No value in Keychain",
            })
            continue
        vr = validate_key(name, value)
        results.append({
            "name": name,
            "provider": vr.provider,
            "status": vr.status,
            "message": vr.message,
        })
    return {"results": results}


# ---------------------------------------------------------------------------
# Tool 5: banto_validate_keychain
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_validate_keychain() -> dict:
    """Scan Keychain for known provider API keys and validate them.

    Searches the login keychain for services matching known provider patterns
    (openai, anthropic, gemini, github, cloudflare, xai), then validates
    each key with a lightweight read-only API call.

    No secret values are returned — only provider name and pass/fail status.
    """
    import re
    import subprocess

    from .sync.validate import SERVICE_PATTERNS, should_exclude, validate_key

    result = subprocess.run(
        ["security", "dump-keychain"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"results": [], "message": "Failed to read Keychain"}

    svce_re = re.compile(r'"svce"<blob>="([^"]*)"')
    acct_re = re.compile(r'"acct"<blob>="([^"]*)"')

    entries_found: list[tuple[str, str]] = []
    current_attrs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("class:"):
            if "svce" in current_attrs:
                entries_found.append((
                    current_attrs.get("svce", ""),
                    current_attrs.get("acct", ""),
                ))
            current_attrs = {}
            continue
        m = svce_re.search(stripped)
        if m:
            current_attrs["svce"] = m.group(1)
        m = acct_re.search(stripped)
        if m:
            current_attrs["acct"] = m.group(1)
    if "svce" in current_attrs:
        entries_found.append((
            current_attrs.get("svce", ""),
            current_attrs.get("acct", ""),
        ))

    # Filter for known provider patterns and validate
    seen: set[str] = set()
    results = []
    for svc, acct in entries_found:
        if not svc or svc in seen or should_exclude(svc):
            continue
        svc_lower = svc.lower()
        for pattern in SERVICE_PATTERNS:
            if pattern in svc_lower:
                seen.add(svc)
                try:
                    val = subprocess.run(
                        ["security", "find-generic-password", "-s", svc, "-w"],
                        capture_output=True, text=True,
                    ).stdout.strip()
                    if val:
                        vr = validate_key(svc, val)
                        results.append({
                            "name": svc,
                            "provider": vr.provider,
                            "status": vr.status,
                            "message": vr.message,
                        })
                    else:
                        results.append({
                            "name": svc,
                            "provider": pattern,
                            "status": "unknown",
                            "message": "Could not retrieve value",
                        })
                except Exception:
                    results.append({
                        "name": svc,
                        "provider": pattern,
                        "status": "unknown",
                        "message": "Error retrieving key",
                    })
                break

    return {"results": results}


# ---------------------------------------------------------------------------
# Tool 6: banto_budget_status
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_budget_status() -> dict:
    """Show current budget status — remaining balance, usage, limits.

    Returns budget breakdown by provider and model.
    If budget is not configured, returns {budget_enabled: false}.
    """
    try:
        from .guard import CostGuard
        guard = CostGuard()
        return guard.get_remaining_budget()
    except (FileNotFoundError, KeyError, Exception) as exc:
        return {
            "budget_enabled": False,
            "message": f"Budget not configured: {type(exc).__name__}",
        }


# ---------------------------------------------------------------------------
# Tool 7: banto_register_key
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_register_key(provider: str = "") -> dict:
    """Open a browser popup for the user to enter an API key securely.

    The popup runs a temporary local HTTP server. The user enters the key
    in the browser — it goes directly to Keychain. The agent never sees
    the key value.

    Args:
        provider: Optional provider hint (e.g. "openai", "anthropic").
                  Pre-fills the provider field in the popup.

    Returns the URL the user should open.
    """
    from .register_popup import serve_register_popup

    url = serve_register_popup(provider_hint=provider, blocking=False)

    return {
        "message": "Browser opened for key registration",
        "url": url,
        "provider": provider or "(any)",
    }


# ---------------------------------------------------------------------------
# Tool 8: banto_lease_list
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_lease_list() -> dict:
    """List active leases (dynamic secrets with TTL).

    Returns metadata only — never the credential values.
    Each lease shows name, TTL, expiry, and remaining seconds.
    """
    from .lease import LeaseManager

    mgr = LeaseManager()
    active = mgr.list_leases()
    return {"leases": active}


# ---------------------------------------------------------------------------
# Tool 9: banto_lease_cleanup
# ---------------------------------------------------------------------------
@mcp.tool()
async def banto_lease_cleanup() -> dict:
    """Revoke all expired leases to free Keychain entries.

    Runs revocation commands for expired leases and removes
    them from Keychain.
    """
    from .lease import LeaseManager

    mgr = LeaseManager()
    revoked = mgr.cleanup()
    return {"revoked_count": revoked}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the banto MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()

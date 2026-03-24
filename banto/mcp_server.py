# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""MCP server for banto — exposes secret management tools to AI agents.

Compatible with:
  - Claude Code (stdio transport)
  - OpenAI Apps SDK (HTTP/SSE transport)
  - Any MCP-compatible client

Critical design: agents NEVER see secret values.
All tools return metadata, status, and results — never API key values.

Each tool returns a dict with:
  - structuredContent: concise JSON for widget rendering and model consumption
  - content: text narration for conversation display

Launch:
    banto-mcp                          # stdio (Claude Code)
    banto-mcp --transport sse          # SSE (Apps SDK dev)
    banto-mcp --transport http --port 8385  # HTTP (Apps SDK prod)
"""
from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "banto",
    description="Local-first secret management — sync, validate, budget, leases",
)


# ── Tool 1: sync_status (read-only) ──────────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
})
async def banto_sync_status() -> dict:
    """Show sync status matrix — which secrets exist in Keychain and each target.

    Returns metadata only. No secret values are ever included.
    Use this to check if secrets are properly deployed.
    """
    from .sync.config import SyncConfig
    from .sync.sync import check_status

    config = SyncConfig.load()
    if not config.secrets:
        return {
            "structuredContent": {
                "secrets": [], "count": 0, "in_sync": 0, "drifted": 0,
            },
            "content": "No secrets configured in sync.json",
        }

    entries = check_status(config)
    secrets = []
    in_sync = 0
    for e in entries:
        targets = {label: status for label, status in e.target_status.items()}
        all_ok = e.keychain_exists and all(v is True for v in targets.values())
        if all_ok:
            in_sync += 1
        secrets.append({
            "name": e.secret_name,
            "env_name": e.env_name,
            "keychain_exists": e.keychain_exists,
            "targets": targets,
        })

    total = len(secrets)
    drifted = total - in_sync

    return {
        "structuredContent": {
            "secrets": secrets,
            "count": total,
            "in_sync": in_sync,
            "drifted": drifted,
        },
        "content": (
            f"{total} secrets configured, {in_sync} in sync, "
            f"{drifted} drifted"
        ),
    }


# ── Tool 2: sync_push (destructive) ──────────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": True,
    "openWorldHint": False,
})
async def banto_sync_push(name: str = "") -> dict:
    """Push secrets from Keychain to cloud targets.

    Deploys secrets to configured platforms (Vercel, Cloudflare, AWS, etc.).
    The agent never sees the values — they go directly from Keychain to target.

    Args:
        name: Optional specific secret name. If empty, pushes all secrets.
    """
    from .sync.config import SyncConfig
    from .sync.sync import sync_all, sync_secret

    config = SyncConfig.load()
    if name:
        report = sync_secret(config, name)
    else:
        report = sync_all(config)

    results = [
        {"name": r.secret_name, "target": r.target_label,
         "success": r.success, "message": r.message}
        for r in report.results
    ]

    scope = f"secret '{name}'" if name else "all secrets"
    if report.all_ok:
        summary = f"Pushed {scope}: {report.ok_count} target(s) updated successfully"
    else:
        summary = (
            f"Pushed {scope}: {report.ok_count} OK, "
            f"{report.fail_count} failed"
        )

    return {
        "structuredContent": {
            "ok": report.all_ok,
            "ok_count": report.ok_count,
            "fail_count": report.fail_count,
            "results": results,
        },
        "content": summary,
    }


# ── Tool 3: sync_audit (read-only) ───────────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
})
async def banto_sync_audit(max_age_days: int = 0) -> dict:
    """Check drift, fingerprint changes, and staleness of secrets.

    Compares Keychain state against last-pushed fingerprints and target existence.
    Returns issues found — no secret values, only fingerprint hashes and dates.

    Args:
        max_age_days: If >0, flag secrets not rotated within this many days.
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
        if not entry.keychain_exists:
            issues.append(f"DRIFT {entry.env_name}: missing in Keychain")
            continue
        for label, status in entry.target_status.items():
            if status is False:
                issues.append(f"DRIFT {entry.env_name} -> {label}")

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
                    issues.append(f"STALE {name}: last rotated {age_days}d ago (threshold: {max_age_days}d)")
            except (ValueError, TypeError):
                issues.append(f"STALE {name}: unparseable timestamp")

    ok = len(issues) == 0
    if ok:
        summary = "Audit passed: no drift or staleness detected"
    else:
        summary = f"Audit found {len(issues)} issue(s): " + "; ".join(issues[:3])
        if len(issues) > 3:
            summary += f" (+{len(issues) - 3} more)"

    return {
        "structuredContent": {
            "ok": ok,
            "issue_count": len(issues),
            "issues": issues,
        },
        "content": summary,
    }


# ── Tool 4: validate (read-only, openWorld) ──────────────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": True,
})
async def banto_validate() -> dict:
    """Validate API keys in sync.json against provider endpoints.

    Makes minimal read-only API calls (GET /v1/models etc.) to check validity.
    Returns pass/fail/unknown per key. No secret values in response.

    Supported providers: OpenAI, Anthropic, Gemini, GitHub, Cloudflare, xAI.
    """
    from .keychain import KeychainStore
    from .sync.config import SyncConfig
    from .sync.validate import validate_key

    config = SyncConfig.load()
    if not config.secrets:
        return {
            "structuredContent": {"results": [], "total": 0, "pass": 0, "fail": 0, "unknown": 0},
            "content": "No secrets in sync.json",
        }

    kc = KeychainStore(service_prefix=config.keychain_service)
    results = []
    pass_count = 0
    fail_count = 0
    unknown_count = 0

    for name, entry in config.secrets.items():
        value = kc.get(entry.account)
        if not value:
            results.append({"name": name, "provider": name,
                           "status": "unknown", "message": "Not in Keychain"})
            unknown_count += 1
            continue
        vr = validate_key(name, value)
        results.append({"name": name, "provider": vr.provider,
                        "status": vr.status, "message": vr.message})
        if vr.status == "pass":
            pass_count += 1
        elif vr.status == "fail":
            fail_count += 1
        else:
            unknown_count += 1

    total = len(results)
    summary_parts = []
    if pass_count:
        summary_parts.append(f"{pass_count} passed")
    if fail_count:
        summary_parts.append(f"{fail_count} failed")
    if unknown_count:
        summary_parts.append(f"{unknown_count} unknown")

    return {
        "structuredContent": {
            "results": results,
            "total": total,
            "pass": pass_count,
            "fail": fail_count,
            "unknown": unknown_count,
        },
        "content": f"Validated {total} keys: {', '.join(summary_parts)}",
    }


# ── Tool 5: validate_keychain (read-only, openWorld) ─────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": True,
})
async def banto_validate_keychain() -> dict:
    """Scan Keychain for known provider API keys and validate them.

    Searches for keys matching known providers (OpenAI, Anthropic, Gemini,
    GitHub, Cloudflare, xAI) and tests each with a read-only API call.

    Returns pass/fail/unknown per key. No secret values in response.
    """
    import re
    import subprocess

    from .sync.validate import SERVICE_PATTERNS, should_exclude, validate_key

    result = subprocess.run(
        ["security", "dump-keychain"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {
            "structuredContent": {"results": [], "total": 0, "pass": 0, "fail": 0, "unknown": 0},
            "content": "Failed to read Keychain",
        }

    svce_re = re.compile(r'"svce"<blob>="([^"]*)"')
    acct_re = re.compile(r'"acct"<blob>="([^"]*)"')

    entries_found: list[tuple[str, str]] = []
    current_attrs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("class:"):
            if "svce" in current_attrs:
                entries_found.append((current_attrs.get("svce", ""),
                                     current_attrs.get("acct", "")))
            current_attrs = {}
            continue
        m = svce_re.search(stripped)
        if m:
            current_attrs["svce"] = m.group(1)
        m = acct_re.search(stripped)
        if m:
            current_attrs["acct"] = m.group(1)
    if "svce" in current_attrs:
        entries_found.append((current_attrs.get("svce", ""),
                             current_attrs.get("acct", "")))

    seen: set[str] = set()
    results = []
    pass_count = 0
    fail_count = 0
    unknown_count = 0

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
                        results.append({"name": svc, "provider": vr.provider,
                                        "status": vr.status, "message": vr.message})
                        if vr.status == "pass":
                            pass_count += 1
                        elif vr.status == "fail":
                            fail_count += 1
                        else:
                            unknown_count += 1
                    else:
                        results.append({"name": svc, "provider": pattern,
                                        "status": "unknown", "message": "Could not retrieve"})
                        unknown_count += 1
                except Exception:
                    results.append({"name": svc, "provider": pattern,
                                    "status": "unknown", "message": "Retrieval error"})
                    unknown_count += 1
                break

    total = len(results)
    summary_parts = []
    if pass_count:
        summary_parts.append(f"{pass_count} passed")
    if fail_count:
        summary_parts.append(f"{fail_count} failed")
    if unknown_count:
        summary_parts.append(f"{unknown_count} unknown")

    return {
        "structuredContent": {
            "results": results,
            "total": total,
            "pass": pass_count,
            "fail": fail_count,
            "unknown": unknown_count,
        },
        "content": (
            f"Scanned Keychain: found {total} provider keys"
            + (f" ({', '.join(summary_parts)})" if summary_parts else "")
        ),
    }


# ── Tool 6: budget_status (read-only) ────────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
})
async def banto_budget_status() -> dict:
    """Show current budget status — remaining balance, usage breakdown.

    Returns budget data by provider and model.
    If budget is not configured, returns {budget_enabled: false}.
    """
    try:
        from .guard import CostGuard
        guard = CostGuard()
        status = guard.get_remaining_budget()
        status["budget_enabled"] = True

        remaining = status.get("remaining_usd", 0)
        used = status.get("used_usd", 0)
        limit = status.get("monthly_limit_usd", 0)
        month = status.get("month", "")

        return {
            "structuredContent": status,
            "content": (
                f"Budget for {month}: ${used:.2f} used of ${limit:.2f} "
                f"(${remaining:.2f} remaining)"
            ),
        }
    except Exception:
        return {
            "structuredContent": {"budget_enabled": False},
            "content": "Budget not configured",
        }


# ── Tool 7: register_key (not destructive, openWorld) ────────────

@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": False,
    "openWorldHint": True,
})
async def banto_register_key(provider: str = "") -> dict:
    """Open a browser popup for the user to enter an API key.

    The popup runs on localhost. The user enters the key in the browser —
    it goes directly to Keychain. The agent NEVER sees the key value.

    After the user enters the key, use banto_validate to verify it works,
    then banto_sync_push to deploy it to cloud targets.

    Args:
        provider: Optional hint (e.g. "openai"). Pre-fills the provider field.
    """
    from .register_popup import serve_register_popup

    url = serve_register_popup(provider_hint=provider, blocking=False)

    return {
        "structuredContent": {
            "provider": provider or "(any)",
            "url": url,
            "status": "popup_opened",
        },
        "content": (
            f"Browser opened for key registration"
            + (f" (provider: {provider})" if provider else "")
            + f". URL: {url}"
        ),
    }


# ── Tool 8: lease_list (read-only) ───────────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
})
async def banto_lease_list() -> dict:
    """List active dynamic leases (short-lived credentials).

    Returns metadata only — never the credential values.
    Each lease shows name, TTL, expiry, and remaining seconds.
    """
    from .lease import LeaseManager

    mgr = LeaseManager()
    active = mgr.list_leases()

    count = len(active)
    if count == 0:
        summary = "No active leases"
    else:
        names = [l.get("name", "?") for l in active[:5]]
        summary = f"{count} active lease(s): {', '.join(names)}"
        if count > 5:
            summary += f" (+{count - 5} more)"

    return {
        "structuredContent": {
            "leases": active,
            "count": count,
        },
        "content": summary,
    }


# ── Tool 9: lease_cleanup (destructive) ──────────────────────────

@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": True,
    "openWorldHint": False,
})
async def banto_lease_cleanup() -> dict:
    """Revoke all expired leases to free Keychain entries.

    Runs revocation commands and removes expired credentials from Keychain.
    """
    from .lease import LeaseManager

    mgr = LeaseManager()
    revoked = mgr.cleanup()

    if revoked == 0:
        summary = "No expired leases to clean up"
    else:
        summary = f"Revoked {revoked} expired lease(s)"

    return {
        "structuredContent": {
            "revoked_count": revoked,
        },
        "content": summary,
    }


# ── Entry point ──────────────────────────────────────────────────

def main() -> None:
    """Run the banto MCP server.

    Supports multiple transports:
        banto-mcp                           # stdio (default, Claude Code)
        banto-mcp --transport sse           # SSE (OpenAI Apps SDK dev)
        banto-mcp --transport http --port 8385  # HTTP (production)
    """
    transport = "stdio"
    port = 8385

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            i += 1

    # Capability URL: if BANTO_MCP_PATH_TOKEN is set, use a secret path
    # instead of the default /mcp. This makes the URL itself the credential.
    path_token = os.environ.get("BANTO_MCP_PATH_TOKEN", "")
    mcp_path = f"/mcp-{path_token}" if path_token else "/mcp"

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "sse":
        mcp.run(transport="sse", sse_path="/sse", port=port)
    elif transport == "http":
        mcp.run(transport="streamable-http", path=mcp_path, port=port)
        if path_token:
            print(f"MCP endpoint: http://127.0.0.1:{port}{mcp_path}",
                  file=sys.stderr)
    else:
        print(f"Unknown transport: {transport}", file=sys.stderr)
        print("Supported: stdio, sse, http", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""ChatGPT Widget UI — inline card templates for Apps SDK.

Each function returns an HTML string suitable for registerAppResource
or structuredContent rendering. All widgets follow ChatGPT card rules:

  - CSS custom properties for theme compat (dark/light)
  - No custom fonts (inherit system)
  - No gradients on surfaces
  - Brand accent ONLY on buttons
  - WCAG AA contrast ratios
  - No nested scrolling or tabs
  - Max 2 primary actions per card
"""
from __future__ import annotations

import html as _html

# ── Shared styles ────────────────────────────────────────────────

_BASE_STYLES = """\
<style>
  .banto-card {
    font-family: inherit;
    color: var(--text-primary, #e6edf3);
    background: var(--surface-primary, #161b22);
    border: 1px solid var(--border-default, #30363d);
    border-radius: 8px;
    padding: 16px;
    max-width: 560px;
    line-height: 1.5;
  }
  .banto-card * {
    box-sizing: border-box;
  }
  .banto-card h3 {
    margin: 0 0 12px 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary, #e6edf3);
  }
  .banto-card table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .banto-card th {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-default, #30363d);
    color: var(--text-secondary, #8b949e);
    font-weight: 500;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .banto-card td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-muted, #21262d);
    color: var(--text-primary, #e6edf3);
    vertical-align: middle;
  }
  .banto-card .stats-row {
    display: flex;
    gap: 16px;
    margin-bottom: 12px;
  }
  .banto-card .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .banto-card .stat-value {
    font-size: 20px;
    font-weight: 600;
    color: var(--text-primary, #e6edf3);
  }
  .banto-card .stat-label {
    font-size: 11px;
    color: var(--text-secondary, #8b949e);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .banto-card .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    line-height: 1.4;
  }
  .banto-card .badge-pass {
    background: rgba(63, 185, 80, 0.15);
    color: #3fb950;
  }
  .banto-card .badge-fail {
    background: rgba(248, 81, 73, 0.15);
    color: #f85149;
  }
  .banto-card .badge-unknown {
    background: rgba(210, 153, 34, 0.15);
    color: #d29922;
  }
  .banto-card .badge-ok {
    color: #3fb950;
  }
  .banto-card .badge-miss {
    color: #f85149;
  }
  .banto-card .actions {
    display: flex;
    gap: 8px;
    margin-top: 14px;
  }
  .banto-card .btn-primary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    background: #2f81f7;
    color: #ffffff;
    font-family: inherit;
    line-height: 1;
  }
  .banto-card .btn-primary:hover {
    background: #388bfd;
  }
  .banto-card .btn-secondary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: 1px solid var(--border-default, #30363d);
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    background: transparent;
    color: var(--text-primary, #e6edf3);
    font-family: inherit;
    line-height: 1;
  }
  .banto-card .btn-secondary:hover {
    background: var(--surface-secondary, #21262d);
  }
  .banto-card .empty {
    text-align: center;
    padding: 24px 0;
    color: var(--text-secondary, #8b949e);
    font-size: 13px;
  }
</style>
"""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return _html.escape(str(text))


# ── Widget 1: Sync Status ───────────────────────────────────────


def sync_status_widget(data: dict) -> str:
    """Inline card showing sync status matrix.

    Args:
        data: Dict with keys:
            secrets: list of {name, env_name, keychain_exists, targets: {label: bool}}
            count: int total count
    """
    secrets = data.get("secrets", [])
    count = data.get("count", len(secrets))
    message = data.get("message", "")

    if not secrets and message:
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f'<h3>Sync Status</h3>'
            f'<div class="empty">{_esc(message)}</div>'
            f"</div>"
        )

    # Calculate stats
    in_sync = 0
    drifted = 0
    for s in secrets:
        kc = s.get("keychain_exists", False)
        targets = s.get("targets", {})
        all_ok = kc and all(v is True for v in targets.values())
        if all_ok:
            in_sync += 1
        else:
            drifted += 1

    # Collect all unique target labels for columns
    all_targets: list[str] = []
    seen: set[str] = set()
    for s in secrets:
        for label in s.get("targets", {}):
            if label not in seen:
                all_targets.append(label)
                seen.add(label)

    # Stats row
    stats_html = (
        f'<div class="stats-row">'
        f'<div class="stat"><span class="stat-value">{count}</span>'
        f'<span class="stat-label">Total</span></div>'
        f'<div class="stat"><span class="stat-value">{in_sync}</span>'
        f'<span class="stat-label">In Sync</span></div>'
        f'<div class="stat"><span class="stat-value">{drifted}</span>'
        f'<span class="stat-label">Drifted</span></div>'
        f"</div>"
    )

    # Table header
    target_headers = "".join(f"<th>{_esc(t)}</th>" for t in all_targets)
    thead = f"<tr><th>Secret</th><th>Keychain</th>{target_headers}</tr>"

    # Table rows
    rows: list[str] = []
    for s in secrets:
        name = _esc(s.get("name", s.get("env_name", "?")))
        kc = s.get("keychain_exists", False)
        kc_cell = '<span class="badge-ok">&#10003;</span>' if kc else '<span class="badge-miss">&#10007;</span>'
        target_cells = ""
        for label in all_targets:
            val = s.get("targets", {}).get(label)
            if val is True:
                target_cells += '<td><span class="badge-ok">&#10003;</span></td>'
            elif val is False:
                target_cells += '<td><span class="badge-miss">&#10007;</span></td>'
            else:
                target_cells += '<td><span style="color:var(--text-secondary,#8b949e)">&#8212;</span></td>'
        rows.append(f"<tr><td>{name}</td><td>{kc_cell}</td>{target_cells}</tr>")

    tbody = "".join(rows)

    # Actions (max 2)
    actions_html = (
        '<div class="actions">'
        '<button class="btn-primary" data-action="banto_sync_push">Sync All</button>'
        '<button class="btn-secondary" data-action="banto_sync_audit">Audit</button>'
        "</div>"
    )

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f"<h3>Sync Status</h3>"
        f"{stats_html}"
        f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"
        f"{actions_html}"
        f"</div>"
    )


# ── Widget 2: Validate Results ──────────────────────────────────


def validate_results_widget(data: dict) -> str:
    """Inline card showing validation results.

    Args:
        data: Dict with keys:
            results: list of {name, provider, status, message}
    """
    results = data.get("results", [])
    message = data.get("message", "")

    if not results and message:
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f'<h3>Key Validation</h3>'
            f'<div class="empty">{_esc(message)}</div>'
            f"</div>"
        )

    if not results:
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f'<h3>Key Validation</h3>'
            f'<div class="empty">No keys to validate</div>'
            f"</div>"
        )

    # Summary counts
    pass_count = sum(1 for r in results if r.get("status") == "pass")
    fail_count = sum(1 for r in results if r.get("status") == "fail")
    unknown_count = sum(1 for r in results if r.get("status") not in ("pass", "fail"))

    stats_html = (
        f'<div class="stats-row">'
        f'<div class="stat"><span class="stat-value">{pass_count}</span>'
        f'<span class="stat-label">Pass</span></div>'
        f'<div class="stat"><span class="stat-value">{fail_count}</span>'
        f'<span class="stat-label">Fail</span></div>'
        f'<div class="stat"><span class="stat-value">{unknown_count}</span>'
        f'<span class="stat-label">Unknown</span></div>'
        f"</div>"
    )

    thead = "<tr><th>Key</th><th>Provider</th><th>Status</th><th>Details</th></tr>"

    rows: list[str] = []
    for r in results:
        name = _esc(r.get("name", "?"))
        provider = _esc(r.get("provider", "?"))
        status = r.get("status", "unknown")
        msg = _esc(r.get("message", ""))

        if status == "pass":
            badge = '<span class="badge badge-pass">PASS</span>'
        elif status == "fail":
            badge = '<span class="badge badge-fail">FAIL</span>'
        else:
            badge = '<span class="badge badge-unknown">UNKNOWN</span>'

        rows.append(f"<tr><td>{name}</td><td>{provider}</td><td>{badge}</td><td>{msg}</td></tr>")

    tbody = "".join(rows)

    actions_html = (
        '<div class="actions">'
        '<button class="btn-primary" data-action="banto_sync_push">Sync Failed</button>'
        '<button class="btn-secondary" data-action="banto_register_key">Register Key</button>'
        "</div>"
    )

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f"<h3>Key Validation</h3>"
        f"{stats_html}"
        f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"
        f"{actions_html}"
        f"</div>"
    )


# ── Widget 3: Register Prompt ───────────────────────────────────


def register_prompt_widget(data: dict) -> str:
    """Simple card prompting user to register a key.

    Args:
        data: Dict with keys:
            provider: str provider name (e.g. "openai")
            url: str localhost URL for popup
            message: str status message
    """
    provider = _esc(data.get("provider", "(any)"))
    url = _esc(data.get("url", ""))
    message = _esc(data.get("message", "Register an API key"))

    provider_display = provider if provider != "(any)" else "API Key"

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f"<h3>Register {provider_display}</h3>"
        f'<p style="margin:0 0 12px 0;font-size:13px;'
        f'color:var(--text-secondary,#8b949e);">{message}</p>'
        f'<p style="margin:0 0 14px 0;font-size:13px;">'
        f"The key is entered in your browser and stored directly in Keychain. "
        f"It never passes through the AI agent.</p>"
        f'<div class="actions">'
        f'<a href="{url}" target="_blank" rel="noopener" class="btn-primary" '
        f'style="text-decoration:none;">Open Registration</a>'
        f"</div>"
        f"</div>"
    )


# ── Widget 4: Budget Status ────────────────────────────────────


def budget_status_widget(data: dict) -> str:
    """Inline card showing budget status.

    Args:
        data: Dict with budget_enabled, remaining_usd, used_usd,
              monthly_limit_usd, by_provider, by_model, etc.
    """
    if not data.get("budget_enabled", False):
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f"<h3>Budget</h3>"
            f'<div class="empty">{_esc(data.get("message", "Budget not configured"))}</div>'
            f"</div>"
        )

    remaining = data.get("remaining_usd", 0)
    used = data.get("used_usd", 0)
    limit = data.get("monthly_limit_usd", 0)
    month = _esc(data.get("month", ""))

    pct_used = (used / limit * 100) if limit > 0 else 0
    bar_color = "#3fb950" if pct_used < 50 else "#d29922" if pct_used < 80 else "#f85149"

    stats_html = (
        f'<div class="stats-row">'
        f'<div class="stat"><span class="stat-value">${remaining:.2f}</span>'
        f'<span class="stat-label">Remaining</span></div>'
        f'<div class="stat"><span class="stat-value">${used:.2f}</span>'
        f'<span class="stat-label">Used</span></div>'
        f'<div class="stat"><span class="stat-value">${limit:.2f}</span>'
        f'<span class="stat-label">Limit</span></div>'
        f"</div>"
    )

    # Usage bar
    bar_html = (
        f'<div style="background:var(--border-muted,#21262d);border-radius:4px;'
        f'height:6px;margin-bottom:14px;overflow:hidden;">'
        f'<div style="background:{bar_color};height:100%;'
        f'width:{min(pct_used, 100):.1f}%;border-radius:4px;"></div>'
        f"</div>"
    )

    # Provider breakdown
    provider_rows = ""
    by_provider = data.get("by_provider", {})
    if by_provider:
        for p, info in by_provider.items():
            p_used = info.get("used_usd", 0)
            p_limit = info.get("limit_usd")
            limit_str = f"${p_limit:.2f}" if p_limit is not None else "no limit"
            provider_rows += (
                f"<tr><td>{_esc(p)}</td><td>${p_used:.2f}</td><td>{limit_str}</td></tr>"
            )

    provider_table = ""
    if provider_rows:
        provider_table = (
            f'<div style="margin-top:10px;font-size:12px;'
            f'color:var(--text-secondary,#8b949e);margin-bottom:4px;">By Provider</div>'
            f"<table><thead><tr><th>Provider</th><th>Used</th><th>Limit</th></tr></thead>"
            f"<tbody>{provider_rows}</tbody></table>"
        )

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f"<h3>Budget &mdash; {month}</h3>"
        f"{stats_html}"
        f"{bar_html}"
        f"{provider_table}"
        f"</div>"
    )


# ── Widget 5: Audit Results ────────────────────────────────────


def audit_results_widget(data: dict) -> str:
    """Inline card showing audit/drift issues.

    Args:
        data: Dict with ok (bool) and issues (list of strings).
    """
    ok = data.get("ok", True)
    issues = data.get("issues", [])

    if ok:
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f"<h3>Audit</h3>"
            f'<div style="display:flex;align-items:center;gap:8px;padding:12px 0;'
            f'color:#3fb950;font-size:14px;">'
            f"<span>&#10003;</span> No drift or staleness detected</div>"
            f"</div>"
        )

    items = "".join(
        f'<li style="padding:4px 0;font-size:13px;">{_esc(i)}</li>'
        for i in issues
    )

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f'<h3>Audit &mdash; {len(issues)} issue{"s" if len(issues) != 1 else ""}</h3>'
        f'<ul style="margin:0;padding:0 0 0 20px;list-style:disc;">{items}</ul>'
        f'<div class="actions">'
        f'<button class="btn-primary" data-action="banto_sync_push">Fix Drift</button>'
        f"</div>"
        f"</div>"
    )


# ── Widget 6: Lease List ───────────────────────────────────────


def lease_list_widget(data: dict) -> str:
    """Inline card showing active leases.

    Args:
        data: Dict with leases (list of lease metadata) and count.
    """
    leases = data.get("leases", [])
    count = data.get("count", len(leases))

    if not leases:
        return (
            f"{_BASE_STYLES}"
            f'<div class="banto-card">'
            f"<h3>Active Leases</h3>"
            f'<div class="empty">No active leases</div>'
            f"</div>"
        )

    thead = "<tr><th>Name</th><th>TTL</th><th>Remaining</th></tr>"
    rows: list[str] = []
    for lease in leases:
        name = _esc(lease.get("name", "?"))
        ttl = lease.get("ttl_seconds", 0)
        remaining = lease.get("remaining_seconds", 0)

        ttl_str = _fmt_duration(ttl)
        rem_str = _fmt_duration(remaining)
        rem_color = "#3fb950" if remaining > 300 else "#d29922" if remaining > 60 else "#f85149"

        rows.append(
            f"<tr><td>{name}</td><td>{ttl_str}</td>"
            f'<td style="color:{rem_color}">{rem_str}</td></tr>'
        )

    tbody = "".join(rows)

    return (
        f"{_BASE_STYLES}"
        f'<div class="banto-card">'
        f"<h3>Active Leases &mdash; {count}</h3>"
        f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"
        f'<div class="actions">'
        f'<button class="btn-primary" data-action="banto_lease_cleanup">Cleanup Expired</button>'
        f"</div>"
        f"</div>"
    )


def _fmt_duration(seconds: int) -> str:
    """Format seconds as human-readable duration."""
    if seconds <= 0:
        return "expired"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m"

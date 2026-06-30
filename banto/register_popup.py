# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
register_popup.py - Browser-based API key registration popup.

Opens a minimal, single-use web page in the user's default browser
for entering an API key. The key is stored in macOS Keychain via
KeychainStore. The server binds to 127.0.0.1 only and shuts down
after one successful registration.

Usage:
    from banto.register_popup import serve_register_popup
    serve_register_popup(provider_hint="openai", blocking=True)
"""

import json
import secrets
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from .keychain import KeychainStore, _validate_provider
from .sync.config import SyncConfig

# Provider -> default env var name mapping.
# Keep in sync with setup.py ENV_TO_KEYCHAIN.
PROVIDER_PRESETS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openai-admin": "OPENAI_ADMIN_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "github": "GITHUB_TOKEN",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "xai": "XAI_API_KEY",
    "xai-management": "XAI_MANAGEMENT_API_KEY",
    "line-channel-token": "LINE_CHANNEL_ACCESS_TOKEN",
    "line-channel-secret": "LINE_CHANNEL_SECRET",
    "line-owner-user-id": "LINE_OWNER_USER_ID",
    "azure-client-id": "AZURE_CLIENT_ID",
    "azure-client-secret": "AZURE_CLIENT_SECRET",
    "azure-tenant-id": "AZURE_TENANT_ID",
    "aws-access": "AWS_ACCESS_KEY_ID",
    "aws-secret": "AWS_SECRET_ACCESS_KEY",
    "stripe": "STRIPE_SECRET_KEY",
    "sendgrid": "SENDGRID_API_KEY",
    "database-url": "DATABASE_URL",
}

PROVIDER_GUIDES: dict[str, dict[str, object]] = {
    "xai": {
        "title": "xAI runtime key",
        "summary": "Use the full-auto rotator when possible. Store a manual key here only when you already created one in xAI.",
        "issuer_url": "https://console.x.ai/team/default/api-keys",
        "automation": "banto sync xai-api-key xai --team-id <team_id> --wait-propagation",
        "batch_example": "xai|XAI_API_KEY=<runtime-api-key>\nxai-management|XAI_MANAGEMENT_API_KEY=<management-api-key>",
        "steps": [
            "Create or select an xAI team API key with the required endpoint/model ACLs.",
            "For future full-auto rotation, also store an xAI Management API key.",
            "Use batch mode to store both the runtime key and management key at once.",
        ],
    },
    "xai-management": {
        "title": "xAI management key",
        "summary": "Required for banto to create, check propagation for, and revoke xAI API keys automatically.",
        "issuer_url": "https://console.x.ai/team/default/api-keys",
        "automation": "banto sync xai-api-key xai --team-id <team_id> --wait-propagation",
        "batch_example": "xai-management|XAI_MANAGEMENT_API_KEY=<management-api-key>\nxai|XAI_API_KEY=<runtime-api-key>",
        "steps": [
            "Create a management-capable key in xAI.",
            "Store it under xai-management so banto can rotate XAI_API_KEY later.",
            "Keep the runtime XAI_API_KEY separate from this management key.",
        ],
    },
    "openai": {
        "title": "OpenAI runtime key",
        "summary": "Use the OpenAI service-account rotator for project-managed keys. Manual entry is a fallback.",
        "issuer_url": "https://platform.openai.com/api-keys",
        "automation": "banto sync openai-service-account openai --project-id <proj_...>",
        "batch_example": "openai|OPENAI_API_KEY=<project-api-key>\nopenai-admin|OPENAI_ADMIN_KEY=<admin-key>",
        "steps": [
            "Prefer project service accounts for automated rotation.",
            "Store an admin key separately as openai-admin when using the rotator.",
            "Store runtime keys as openai.",
        ],
    },
    "openai-admin": {
        "title": "OpenAI admin key",
        "summary": "Required only when using the OpenAI service-account rotator.",
        "issuer_url": "https://platform.openai.com/settings/organization/admin-keys",
        "automation": "banto sync openai-service-account openai --project-id <proj_...>",
        "batch_example": "openai-admin|OPENAI_ADMIN_KEY=<admin-key>\nopenai|OPENAI_API_KEY=<project-api-key>",
        "steps": [
            "Create an admin key with project service-account management permission.",
            "Store it under openai-admin.",
            "Do not use the admin key as the app runtime OPENAI_API_KEY.",
        ],
    },
    "google": {
        "title": "Google API key",
        "summary": "Use the Google API Keys API rotator for project-managed GOOGLE_API_KEY values.",
        "issuer_url": "https://console.cloud.google.com/apis/credentials",
        "automation": "banto sync google-api-key google --project-id <project>",
        "batch_example": "google|GOOGLE_API_KEY=<google-api-key>\ngemini|GEMINI_API_KEY=<gemini-api-key>",
        "steps": [
            "Prefer the Google rotator for project-managed keys.",
            "When a Gemini key shares the same account, opt in with --sync-shared-account-secrets.",
            "Use batch mode when registering Google and Gemini fallbacks manually.",
        ],
    },
    "github": {
        "title": "GitHub token",
        "summary": "GitHub issuance is still manual in banto; this screen reduces registration work after you create the token.",
        "issuer_url": "https://github.com/settings/tokens",
        "automation": "",
        "batch_example": "github|GITHUB_TOKEN=<github-token>",
        "steps": [
            "Create a fine-grained token with the minimum required repositories and permissions.",
            "Paste it here as github.",
            "Use banto sync propagate github after replacing the stored value.",
        ],
    },
    "cloudflare": {
        "title": "Cloudflare API token",
        "summary": "Cloudflare issuance is still manual in banto; store the new token here and propagate with banto sync.",
        "issuer_url": "https://dash.cloudflare.com/profile/api-tokens",
        "automation": "",
        "batch_example": "cloudflare|CLOUDFLARE_API_TOKEN=<cloudflare-token>",
        "steps": [
            "Create a scoped API token in Cloudflare.",
            "Paste it here as cloudflare.",
            "Use banto sync propagate cloudflare for configured targets.",
        ],
    },
    "stripe": {
        "title": "Stripe secret key",
        "summary": "Stripe secret key issuance is manual; webhook secrets remain a manual-cutover runbook item.",
        "issuer_url": "https://dashboard.stripe.com/apikeys",
        "automation": "",
        "batch_example": "stripe|STRIPE_SECRET_KEY=<stripe-secret-key>",
        "steps": [
            "Create a restricted key where possible.",
            "Store runtime secret keys as stripe.",
            "Do not blind-overwrite webhook verification secrets.",
        ],
    },
}

ASC_REGISTER_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("asc-issuer-id", "ASC_ISSUER_ID", "Issuer ID", "App Store Connect issuer UUID"),
    ("asc-key-name", "ASC_KEY_NAME", "Name", "Human-readable key name in App Store Connect"),
    ("asc-key-id", "ASC_KEY_ID", "Key ID", "App Store Connect key identifier"),
    ("asc-p8-path", "ASC_AUTH_KEY_PATH", ".p8 file path", "Local path to the downloaded AuthKey .p8 file"),
)


def _normalize_register_item(data: dict) -> dict[str, str]:
    """Validate a register payload without leaking the secret value."""
    provider = (data.get("provider") or "").strip()
    value = data.get("value") or ""
    env_name = (data.get("env_name") or "").strip()
    description = (data.get("description") or "").strip()

    if not provider:
        raise ValueError("Provider is required")
    if not value:
        raise ValueError("API key is required")

    try:
        provider = _validate_provider(provider)
    except ValueError as exc:
        raise ValueError(
            "Invalid provider name. Use only letters, digits, hyphens, and underscores."
        ) from exc

    return {
        "provider": provider,
        "value": value,
        "env_name": env_name,
        "description": description,
    }


def _safe_attr(value: str) -> str:
    """Escape a value for safe embedding in an HTML attribute."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_html(provider_hint: str | None = None) -> str:
    """Build the single-page HTML for the registration form."""
    presets_json = json.dumps(PROVIDER_PRESETS)
    guides_json = json.dumps(PROVIDER_GUIDES)
    hint_attr = _safe_attr(provider_hint or "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>banto - Store API Key</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    background: #0f0f14;
    color: #e4e4e7;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}

  .card {{
    background: #1c1c24;
    border: 1px solid #2a2a35;
    border-radius: 16px;
    padding: 40px 36px 36px;
    width: 100%;
    max-width: 560px;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.4),
                0 0 0 1px rgba(255, 255, 255, 0.04);
  }}

  .logo {{
    text-align: center;
    margin-bottom: 28px;
  }}

  .logo-text {{
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}

  .logo-sub {{
    font-size: 13px;
    color: #71717a;
    margin-top: 4px;
  }}

  .guide {{
    margin: 0 0 18px;
    padding: 14px;
    background: #14141b;
    border: 1px solid #2a2a35;
    border-radius: 12px;
    display: none;
  }}

  .guide-title {{
    font-size: 14px;
    font-weight: 700;
    color: #f4f4f5;
    margin-bottom: 4px;
  }}

  .guide-summary {{
    font-size: 12px;
    line-height: 1.5;
    color: #a1a1aa;
    margin-bottom: 10px;
  }}

  .guide-actions {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
  }}

  .guide-link, .guide-code {{
    display: inline-flex;
    align-items: center;
    min-height: 28px;
    padding: 6px 9px;
    border-radius: 8px;
    border: 1px solid #30303d;
    color: #c4b5fd;
    background: #111118;
    font-size: 12px;
    text-decoration: none;
  }}

  .guide-code {{
    color: #d4d4d8;
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  }}

  .guide-steps {{
    margin-left: 16px;
    color: #a1a1aa;
    font-size: 12px;
    line-height: 1.5;
  }}

  .mode-row {{
    display: flex;
    gap: 8px;
    align-items: center;
    color: #a1a1aa;
    font-size: 13px;
  }}

  .mode-row input {{
    width: 16px;
    height: 16px;
    accent-color: #8b5cf6;
  }}

  .batch-help {{
    font-size: 12px;
    color: #71717a;
    margin-top: 6px;
    line-height: 1.45;
  }}

  #batch-entries {{
    min-height: 96px;
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 12px;
  }}

  .field {{
    margin-bottom: 18px;
  }}

  label {{
    display: block;
    font-size: 13px;
    font-weight: 500;
    color: #a1a1aa;
    margin-bottom: 6px;
  }}

  select, input[type="text"], textarea {{
    width: 100%;
    padding: 10px 14px;
    font-size: 14px;
    font-family: inherit;
    color: #e4e4e7;
    background: #111118;
    border: 1px solid #2a2a35;
    border-radius: 10px;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
  }}

  select:focus, input:focus, textarea:focus {{
    border-color: #6366f1;
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
  }}

  select {{
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2371717a' d='M3 4.5L6 7.5L9 4.5'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 32px;
  }}

  .key-wrap {{
    position: relative;
  }}

  .key-wrap input {{
    padding-right: 44px;
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 13px;
    letter-spacing: 0.3px;
  }}

  /* Mask API key text without type="password" to prevent browser
     password managers (especially Safari) from auto-saving to
     iCloud Keychain as an Internet Password. */
  .key-masked {{
    -webkit-text-security: disc;
    text-security: disc;
  }}

  .toggle-vis {{
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: #71717a;
    cursor: pointer;
    padding: 4px;
    font-size: 16px;
    line-height: 1;
    transition: color 0.15s;
  }}

  .toggle-vis:hover {{
    color: #a1a1aa;
  }}

  textarea {{
    resize: vertical;
    min-height: 38px;
    max-height: 80px;
  }}

  .btn {{
    width: 100%;
    padding: 12px;
    font-size: 15px;
    font-weight: 600;
    font-family: inherit;
    color: #fff;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border: none;
    border-radius: 10px;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.1s;
    margin-top: 6px;
  }}

  .btn:hover {{ opacity: 0.9; }}
  .btn:active {{ transform: scale(0.98); }}
  .btn:disabled {{
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
  }}

  .result {{
    text-align: center;
    margin-top: 18px;
    padding: 16px;
    border-radius: 10px;
    font-size: 14px;
    display: none;
  }}

  .result.success {{
    display: block;
    background: rgba(34, 197, 94, 0.08);
    border: 1px solid rgba(34, 197, 94, 0.2);
    color: #4ade80;
  }}

  .result.error {{
    display: block;
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #f87171;
  }}

  .check {{
    font-size: 32px;
    display: block;
    margin-bottom: 6px;
  }}

  .close-hint {{
    font-size: 12px;
    color: #71717a;
    margin-top: 8px;
  }}

  .form-hidden {{ display: none; }}

  @media (max-width: 480px) {{
    .card {{ padding: 28px 20px 24px; }}
  }}
</style>
</head>
<body>

<div class="card">
  <div class="logo">
    <div class="logo-text">banto</div>
    <div class="logo-sub">Store API Key in Keychain</div>
  </div>

  <form id="form" autocomplete="off" data-hint="{hint_attr}">
    <div class="field">
      <label for="provider">Provider</label>
      <select id="provider">
        <option value="">Select a provider...</option>
        <optgroup label="AI / LLM">
          <option value="openai">OpenAI</option>
          <option value="openai-admin">OpenAI Admin Key</option>
          <option value="anthropic">Anthropic</option>
          <option value="gemini">Google Gemini</option>
          <option value="google">Google API</option>
          <option value="xai">xAI (Grok)</option>
          <option value="xai-management">xAI Management Key</option>
        </optgroup>
        <optgroup label="Cloud / Infra">
          <option value="aws-access">AWS Access Key</option>
          <option value="aws-secret">AWS Secret Key</option>
          <option value="azure-client-id">Azure Client ID</option>
          <option value="azure-client-secret">Azure Client Secret</option>
          <option value="azure-tenant-id">Azure Tenant ID</option>
          <option value="cloudflare">Cloudflare</option>
        </optgroup>
        <optgroup label="Developer Tools">
          <option value="github">GitHub</option>
          <option value="stripe">Stripe</option>
          <option value="sendgrid">SendGrid</option>
          <option value="database-url">Database URL</option>
        </optgroup>
        <optgroup label="LINE">
          <option value="line-channel-token">LINE Channel Token</option>
          <option value="line-channel-secret">LINE Channel Secret</option>
          <option value="line-owner-user-id">LINE Owner User ID</option>
        </optgroup>
        <option value="_custom">Custom...</option>
      </select>
    </div>

    <div class="guide" id="provider-guide"></div>

    <div class="field">
      <label class="mode-row" for="batch-mode">
        <input type="checkbox" id="batch-mode">
        Register multiple keys at once
      </label>
      <div class="batch-help">
        Use one line per key: <span class="guide-code">provider|ENV_NAME=value</span>.
        The secret value may contain additional equals signs.
      </div>
    </div>

    <div class="field" id="batch-field" style="display:none">
      <label for="batch-entries">Batch keys</label>
      <textarea id="batch-entries" rows="5"
                placeholder="xai|XAI_API_KEY=...\nxai-management|XAI_MANAGEMENT_API_KEY=..."
                autocomplete="off" spellcheck="false"></textarea>
    </div>

    <div class="field" id="custom-provider-field" style="display:none">
      <label for="custom-provider">Custom Provider Name</label>
      <input type="text" id="custom-provider"
             placeholder="e.g. my-service"
             pattern="[a-zA-Z0-9_-]+" autocomplete="off">
    </div>

    <div class="field">
      <label for="env-name">Env Variable Name</label>
      <input type="text" id="env-name"
             placeholder="e.g. OPENAI_API_KEY" autocomplete="off">
    </div>

    <div class="field">
      <label for="api-key">API Key</label>
      <div class="key-wrap">
        <input type="text" id="api-key" class="key-masked"
               placeholder="sk-..." autocomplete="off"
               spellcheck="false" autocorrect="off" autocapitalize="off">
        <button type="button" class="toggle-vis" id="toggle-vis"
                aria-label="Toggle visibility">&#x25CF;</button>
      </div>
    </div>

    <div class="field">
      <label for="description">Description <span style="color:#52525b">(optional)</span></label>
      <textarea id="description" rows="1"
                placeholder="e.g. Production key" autocomplete="off"></textarea>
    </div>

    <button type="submit" class="btn" id="submit-btn">Store in Keychain</button>
  </form>

  <div class="result" id="result"></div>
</div>

<script>
(function() {{
  const PRESETS = {presets_json};
  const GUIDES = {guides_json};
  const HINT = document.getElementById("form").dataset.hint || "";

  const providerEl   = document.getElementById("provider");
  const guideEl      = document.getElementById("provider-guide");
  const batchModeEl  = document.getElementById("batch-mode");
  const batchField   = document.getElementById("batch-field");
  const batchEl      = document.getElementById("batch-entries");
  const customField   = document.getElementById("custom-provider-field");
  const customEl      = document.getElementById("custom-provider");
  const envNameEl     = document.getElementById("env-name");
  const apiKeyEl      = document.getElementById("api-key");
  const descEl        = document.getElementById("description");
  const formEl        = document.getElementById("form");
  const submitBtn     = document.getElementById("submit-btn");
  const resultEl      = document.getElementById("result");
  const toggleBtn     = document.getElementById("toggle-vis");

  // Apply hint — if it matches a preset, select it; otherwise use Custom.
  // HINT comes from a data-attribute (HTML-escaped), never inline JS.
  if (HINT) {{
    const matched = Array.from(providerEl.options).some(function(o) {{
      return o.value === HINT;
    }});
    if (matched) {{
      providerEl.value = HINT;
      onProviderChange();
    }} else {{
      providerEl.value = "_custom";
      customField.style.display = "";
      customEl.value = HINT;
      envNameEl.value = "";
    }}
  }}

  providerEl.addEventListener("change", onProviderChange);
  batchModeEl.addEventListener("change", onBatchModeChange);

  function onProviderChange() {{
    const v = providerEl.value;
    if (v === "_custom") {{
      customField.style.display = "";
      customEl.focus();
      envNameEl.value = "";
    }} else {{
      customField.style.display = "none";
      customEl.value = "";
      envNameEl.value = PRESETS[v] || "";
    }}
    updateProviderGuide(v);
  }}

  function onBatchModeChange() {{
    const enabled = batchModeEl.checked;
    batchField.style.display = enabled ? "" : "none";
    submitBtn.textContent = enabled ? "Store Keys in Keychain" : "Store in Keychain";
    const guide = GUIDES[providerEl.value];
    if (enabled && guide && guide.batch_example && !batchEl.value.trim()) {{
      batchEl.value = guide.batch_example;
    }}
  }}

  function escapeHtml(value) {{
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#x27;");
  }}

  function updateProviderGuide(provider) {{
    const guide = GUIDES[provider];
    if (!guide) {{
      guideEl.style.display = "none";
      guideEl.innerHTML = "";
      return;
    }}

    let html = '<div class="guide-title">' + escapeHtml(guide.title) + '</div>'
      + '<div class="guide-summary">' + escapeHtml(guide.summary) + '</div>'
      + '<div class="guide-actions">';
    if (guide.issuer_url) {{
      html += '<a class="guide-link" href="' + escapeHtml(guide.issuer_url)
        + '" target="_blank" rel="noreferrer">Open issuer console</a>';
    }}
    if (guide.automation) {{
      html += '<span class="guide-code">' + escapeHtml(guide.automation) + '</span>';
    }}
    html += '</div>';
    if (Array.isArray(guide.steps) && guide.steps.length) {{
      html += '<ol class="guide-steps">';
      guide.steps.forEach(function(step) {{
        html += '<li>' + escapeHtml(step) + '</li>';
      }});
      html += '</ol>';
    }}
    guideEl.innerHTML = html;
    guideEl.style.display = "block";
    if (batchModeEl.checked && guide.batch_example && !batchEl.value.trim()) {{
      batchEl.value = guide.batch_example;
    }}
  }}

  // Toggle key visibility via CSS class (not type="password" which
  // triggers Safari's iCloud Keychain auto-save)
  let visible = false;
  toggleBtn.addEventListener("click", function() {{
    visible = !visible;
    if (visible) {{
      apiKeyEl.classList.remove("key-masked");
    }} else {{
      apiKeyEl.classList.add("key-masked");
    }}
    toggleBtn.textContent = visible ? "\\u25CB" : "\\u25CF";
  }});

  // Submit
  formEl.addEventListener("submit", async function(e) {{
    e.preventDefault();
    resultEl.className = "result";
    resultEl.style.display = "none";

    submitBtn.disabled = true;
    submitBtn.textContent = batchModeEl.checked ? "Storing keys..." : "Storing...";

    try {{
      const csrfResp = await fetch("/api/csrf-token");
      const csrfData = await csrfResp.json();
      const csrfToken = csrfData.token;

      let resp;
      let providerLabel;
      if (batchModeEl.checked) {{
        const items = parseBatchEntries(batchEl.value);
        if (!items.length) {{
          showError("Please enter at least one batch line.");
          submitBtn.disabled = false;
          submitBtn.textContent = "Store Keys in Keychain";
          return;
        }}
        providerLabel = items.length + " key(s)";
        resp = await fetch("/register-many", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken
          }},
          body: JSON.stringify({{ items: items }})
        }});
      }} else {{
        const provider = providerEl.value === "_custom"
          ? customEl.value.trim()
          : providerEl.value;

        if (!provider) {{
          showError("Please select or enter a provider.");
          submitBtn.disabled = false;
          submitBtn.textContent = "Store in Keychain";
          return;
        }}

        const value = apiKeyEl.value;
        if (!value) {{
          showError("Please enter an API key.");
          submitBtn.disabled = false;
          submitBtn.textContent = "Store in Keychain";
          return;
        }}

        providerLabel = provider;
        resp = await fetch("/register", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken
          }},
          body: JSON.stringify({{
            provider: provider,
            env_name: envNameEl.value.trim(),
            value: value,
            description: descEl.value.trim()
          }})
        }});
      }}

      const data = await resp.json();
      if (data.ok) {{
        formEl.classList.add("form-hidden");
        resultEl.innerHTML = '<span class="check">\\u2714</span>'
          + '<strong>Stored securely</strong><br>'
          + '<span style="font-size:13px;color:#a1a1aa">'
          + providerLabel + ' &rarr; Keychain</span>'
          + '<div class="close-hint">You can close this tab.</div>';
        resultEl.className = "result success";
      }} else {{
        showError(data.error || "Failed to store key.");
        submitBtn.disabled = false;
        submitBtn.textContent = batchModeEl.checked ? "Store Keys in Keychain" : "Store in Keychain";
      }}
    }} catch (err) {{
      showError(err.message || "Connection error. Is the server running?");
      submitBtn.disabled = false;
      submitBtn.textContent = batchModeEl.checked ? "Store Keys in Keychain" : "Store in Keychain";
    }}
  }});

  function parseBatchEntries(text) {{
    const lines = text.split(/\\r?\\n/);
    const items = [];
    lines.forEach(function(rawLine) {{
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) {{
        return;
      }}
      const eq = line.indexOf("=");
      if (eq <= 0) {{
        throw new Error("Invalid batch line: " + line);
      }}
      const left = line.slice(0, eq).trim();
      const value = line.slice(eq + 1);
      const pipe = left.indexOf("|");
      const provider = (pipe >= 0 ? left.slice(0, pipe) : left).trim();
      const envName = (pipe >= 0 ? left.slice(pipe + 1) : (PRESETS[provider] || "")).trim();
      items.push({{
        provider: provider,
        env_name: envName,
        value: value,
        description: "Batch registration"
      }});
    }});
    return items;
  }}

  function showError(msg) {{
    resultEl.textContent = msg;
    resultEl.className = "result error";
  }}
}})();
</script>
</body>
</html>"""


def _build_asc_html() -> str:
    """Build the dedicated App Store Connect credential registration form."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>banto - Store App Store Connect Credentials</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    background: #101014;
    color: #f4f4f5;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .card {
    width: 100%;
    max-width: 760px;
    background: #1d1d25;
    border: 1px solid #30303a;
    border-radius: 16px;
    padding: 34px;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.36);
  }
  .eyebrow {
    color: #93c5fd;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  h1 {
    font-size: 28px;
    line-height: 1.2;
    margin-bottom: 10px;
  }
  .intro {
    color: #cbd5e1;
    line-height: 1.55;
    margin-bottom: 24px;
  }
  .notice {
    background: #111827;
    border: 1px solid #374151;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 22px;
    color: #d1d5db;
    font-size: 14px;
    line-height: 1.5;
  }
  label {
    display: block;
    font-size: 14px;
    font-weight: 650;
    color: #f8fafc;
    margin-bottom: 8px;
  }
  .hint {
    color: #94a3b8;
    font-size: 12px;
    margin-top: -3px;
    margin-bottom: 8px;
  }
  input {
    width: 100%;
    min-height: 46px;
    border-radius: 10px;
    border: 1px solid #3f3f46;
    background: #111118;
    color: #f8fafc;
    padding: 12px 14px;
    font: inherit;
    outline: none;
  }
  input:focus {
    border-color: #60a5fa;
    box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.18);
  }
  .field {
    margin-bottom: 18px;
  }
  .actions {
    display: flex;
    gap: 12px;
    align-items: center;
    margin-top: 26px;
  }
  button {
    appearance: none;
    border: none;
    border-radius: 10px;
    background: #2563eb;
    color: #fff;
    min-height: 46px;
    padding: 0 18px;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
  }
  button:disabled {
    opacity: .58;
    cursor: not-allowed;
  }
  .secondary {
    color: #94a3b8;
    font-size: 13px;
  }
  .result {
    display: none;
    margin-top: 20px;
    padding: 14px 16px;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.5;
  }
  .result.success {
    display: block;
    border: 1px solid #14532d;
    background: #052e16;
    color: #dcfce7;
  }
  .result.error {
    display: block;
    border: 1px solid #7f1d1d;
    background: #450a0a;
    color: #fee2e2;
  }
  code {
    color: #bfdbfe;
  }
</style>
</head>
<body>
  <main class="card">
    <div class="eyebrow">App Store Connect</div>
    <h1>Store ASC credentials in Keychain</h1>
    <p class="intro">
      Enter the App Store Connect API key metadata once. banto stores the values
      in macOS Keychain and does not print them back to the page or terminal.
    </p>
    <div class="notice">
      Stored providers: <code>asc-issuer-id</code>, <code>asc-key-name</code>,
      <code>asc-key-id</code>, and <code>asc-p8-path</code>.
    </div>
    <form id="asc-form" autocomplete="off">
      <div class="field">
        <label for="issuer-id">Issuer ID</label>
        <div class="hint">UUID from App Store Connect Users and Access.</div>
        <input id="issuer-id" name="issuer-id" type="text" required autocomplete="off">
      </div>
      <div class="field">
        <label for="key-name">Name</label>
        <div class="hint">Human-readable key name shown in App Store Connect.</div>
        <input id="key-name" name="key-name" type="text" required autocomplete="off">
      </div>
      <div class="field">
        <label for="key-id">Key ID</label>
        <div class="hint">10-character key identifier for the downloaded API key.</div>
        <input id="key-id" name="key-id" type="text" required autocomplete="off">
      </div>
      <div class="field">
        <label for="p8-path">.p8 file path</label>
        <div class="hint">Example: ~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8</div>
        <input id="p8-path" name="p8-path" type="text" required autocomplete="off">
      </div>
      <div class="actions">
        <button id="submit-btn" type="submit">Store ASC Credentials</button>
        <span class="secondary">Values stay local in Keychain.</span>
      </div>
    </form>
    <div id="result" class="result" role="status" aria-live="polite"></div>
  </main>
<script>
(function() {
  const form = document.getElementById("asc-form");
  const button = document.getElementById("submit-btn");
  const result = document.getElementById("result");
  let csrfToken = "";

  fetch("/api/csrf-token", { credentials: "same-origin" })
    .then(function(resp) { return resp.json(); })
    .then(function(data) { csrfToken = data.token || ""; })
    .catch(function() { csrfToken = ""; });

  function value(id) {
    return document.getElementById(id).value.trim();
  }

  function showError(message) {
    result.textContent = message;
    result.className = "result error";
  }

  form.addEventListener("submit", async function(event) {
    event.preventDefault();
    result.textContent = "";
    result.className = "result";

    const issuerId = value("issuer-id");
    const keyName = value("key-name");
    const keyId = value("key-id");
    const p8Path = value("p8-path");
    if (!issuerId || !keyName || !keyId || !p8Path) {
      showError("All fields are required.");
      return;
    }
    if (!p8Path.endsWith(".p8")) {
      showError("The .p8 file path must end with .p8.");
      return;
    }

    button.disabled = true;
    button.textContent = "Storing...";
    try {
      const resp = await fetch("/register-many", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken
        },
        body: JSON.stringify({
          items: [
            {
              provider: "asc-issuer-id",
              env_name: "ASC_ISSUER_ID",
              value: issuerId,
              description: "App Store Connect issuer UUID"
            },
            {
              provider: "asc-key-name",
              env_name: "ASC_KEY_NAME",
              value: keyName,
              description: "App Store Connect key name"
            },
            {
              provider: "asc-key-id",
              env_name: "ASC_KEY_ID",
              value: keyId,
              description: "App Store Connect key identifier"
            },
            {
              provider: "asc-p8-path",
              env_name: "ASC_AUTH_KEY_PATH",
              value: p8Path,
              description: "App Store Connect AuthKey p8 file path"
            }
          ]
        })
      });
      const data = await resp.json();
      if (!data.ok) {
        throw new Error(data.error || "Failed to store ASC credentials.");
      }
      form.style.display = "none";
      result.innerHTML = "ASC credentials stored in Keychain. Stored providers: "
        + "<code>asc-issuer-id</code>, <code>asc-key-name</code>, "
        + "<code>asc-key-id</code>, <code>asc-p8-path</code>. "
        + "You can close this tab.";
      result.className = "result success";
    } catch (err) {
      showError(err.message || "Connection error. Is the server running?");
      button.disabled = false;
      button.textContent = "Store ASC Credentials";
    }
  });
})();
</script>
</body>
</html>"""


class _RegisterHandler(BaseHTTPRequestHandler):
    """Single-use HTTP handler for the registration popup."""

    html_content: str = ""
    keychain: KeychainStore | None = None
    on_success: object = None  # callable or None
    csrf_token: str = ""
    server_port: int = 0

    def _check_origin(self) -> bool:
        """Reject POST requests from foreign origins (CSRF defense)."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # Same-origin requests may omit Origin
        allowed = {
            f"http://127.0.0.1:{self.server_port}",
            f"http://localhost:{self.server_port}",
        }
        return origin in allowed

    def _check_csrf(self) -> bool:
        """Validate X-CSRF-Token header against session token."""
        token = self.headers.get("X-CSRF-Token", "")
        return token == self.csrf_token

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = self.html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/csrf-token":
            self._json_response(200, {"token": self.csrf_token})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/register", "/register-many"):
            self.send_error(404)
            return

        # Origin check (CSRF defense)
        if not self._check_origin():
            self._json_response(403, {"ok": False, "error": "Forbidden: invalid origin"})
            return

        # Content-Type enforcement
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            self._json_response(415, {"ok": False, "error": "Unsupported Media Type: expected application/json"})
            return

        # CSRF token check
        if not self._check_csrf():
            self._json_response(403, {"ok": False, "error": "Forbidden: invalid or missing CSRF token"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._json_response(400, {"ok": False, "error": "Invalid Content-Length"})
            return
        max_payload = 262144 if path == "/register-many" else 65536
        if length > max_payload:
            self._json_response(400, {"ok": False, "error": "Payload too large"})
            return

        try:
            raw = self.rfile.read(length)
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"ok": False, "error": "Invalid JSON"})
            return
        if not isinstance(data, dict):
            self._json_response(400, {"ok": False, "error": "Invalid JSON object"})
            return

        keychain = self.__class__.keychain
        if keychain is None:
            self._json_response(
                500, {"ok": False, "error": "Keychain not available"}
            )
            return

        try:
            if path == "/register-many":
                items = data.get("items")
                if not isinstance(items, list) or not items:
                    raise ValueError("At least one item is required")
                if len(items) > 25:
                    raise ValueError("At most 25 keys can be registered at once")
                normalized = [_normalize_register_item(item) for item in items if isinstance(item, dict)]
                if len(normalized) != len(items):
                    raise ValueError("Each batch item must be an object")
            else:
                normalized = [_normalize_register_item(data)]
        except ValueError as exc:
            self._json_response(400, {"ok": False, "error": str(exc)})
            return

        stored: list[str] = []
        for item in normalized:
            if not keychain.store(item["provider"], item["value"]):
                self._json_response(
                    500,
                    {"ok": False, "error": f"Failed to store {item['provider']} in Keychain"},
                )
                return
            stored.append(item["provider"])

        if stored:
            self._json_response(200, {"ok": True, "count": len(stored), "providers": stored})
            callback = self.__class__.on_success
            if callable(callback):
                threading.Thread(target=callback, daemon=True).start()
        else:
            self._json_response(
                500,
                {"ok": False, "error": "Failed to store in Keychain"},
            )

    def _json_response(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        """Suppress default stderr logging (no secret leaks)."""
        pass


def serve_register_popup(
    *,
    provider_hint: str | None = None,
    blocking: bool = False,
) -> str:
    """Start a single-use registration popup server and open the browser.

    Args:
        provider_hint: Pre-select this provider in the dropdown.
        blocking: If True, block until registration completes or server stops.

    Returns:
        The URL of the popup (e.g. "http://127.0.0.1:54321").
    """
    html = _build_html(provider_hint)
    config = SyncConfig.load()
    keychain = KeychainStore(service_prefix=config.keychain_service)
    csrf_token = secrets.token_urlsafe(32)

    # Build a handler class with our state
    class Handler(_RegisterHandler):
        html_content = html

    Handler.keychain = keychain

    # Bind directly with port 0 to avoid TOCTOU race between
    # finding a free port and actually binding to it.
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]

    Handler.csrf_token = csrf_token
    Handler.server_port = port

    url = f"http://127.0.0.1:{port}"

    done_event = threading.Event()

    def _shutdown() -> None:
        """Delayed shutdown to let the response finish."""
        import time
        time.sleep(0.5)
        done_event.set()
        server.shutdown()

    Handler.on_success = _shutdown

    webbrowser.open(url)

    if blocking:
        # Run server until success or keyboard interrupt
        server_thread = threading.Thread(
            target=server.serve_forever, daemon=True
        )
        server_thread.start()
        try:
            done_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server_thread.join(timeout=2)
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()

    return url


def serve_asc_register_popup(*, blocking: bool = False) -> str:
    """Start the dedicated App Store Connect credential registration popup."""
    html = _build_asc_html()
    config = SyncConfig.load()
    keychain = KeychainStore(service_prefix=config.keychain_service)
    csrf_token = secrets.token_urlsafe(32)

    class Handler(_RegisterHandler):
        html_content = html

    Handler.keychain = keychain

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]

    Handler.csrf_token = csrf_token
    Handler.server_port = port

    url = f"http://127.0.0.1:{port}"

    done_event = threading.Event()

    def _shutdown() -> None:
        import time
        time.sleep(0.5)
        done_event.set()
        server.shutdown()

    Handler.on_success = _shutdown

    webbrowser.open(url)

    if blocking:
        server_thread = threading.Thread(
            target=server.serve_forever, daemon=True
        )
        server_thread.start()
        try:
            done_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server_thread.join(timeout=2)
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()

    return url

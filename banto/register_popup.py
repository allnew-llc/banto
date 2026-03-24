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
import socket
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from .keychain import KeychainStore, _validate_provider

# Provider -> default env var name mapping
PROVIDER_PRESETS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "github": "GITHUB_TOKEN",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "xai": "XAI_API_KEY",
    "aws-access": "AWS_ACCESS_KEY_ID",
    "aws-secret": "AWS_SECRET_ACCESS_KEY",
    "stripe": "STRIPE_SECRET_KEY",
}


def _build_html(provider_hint: str | None = None) -> str:
    """Build the single-page HTML for the registration form."""
    presets_json = json.dumps(PROVIDER_PRESETS)
    hint_json = json.dumps(provider_hint or "")

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
    max-width: 440px;
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

  select, input[type="text"], input[type="password"], textarea {{
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

  <form id="form" autocomplete="off">
    <div class="field">
      <label for="provider">Provider</label>
      <select id="provider">
        <option value="">Select a provider...</option>
        <option value="openai">OpenAI</option>
        <option value="anthropic">Anthropic</option>
        <option value="gemini">Google Gemini</option>
        <option value="github">GitHub</option>
        <option value="cloudflare">Cloudflare</option>
        <option value="xai">xAI (Grok)</option>
        <option value="aws-access">AWS Access Key</option>
        <option value="aws-secret">AWS Secret Key</option>
        <option value="stripe">Stripe</option>
        <option value="_custom">Custom...</option>
      </select>
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
        <input type="password" id="api-key"
               placeholder="sk-..." autocomplete="off" spellcheck="false">
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
  const HINT = {hint_json};

  const providerEl   = document.getElementById("provider");
  const customField   = document.getElementById("custom-provider-field");
  const customEl      = document.getElementById("custom-provider");
  const envNameEl     = document.getElementById("env-name");
  const apiKeyEl      = document.getElementById("api-key");
  const descEl        = document.getElementById("description");
  const formEl        = document.getElementById("form");
  const submitBtn     = document.getElementById("submit-btn");
  const resultEl      = document.getElementById("result");
  const toggleBtn     = document.getElementById("toggle-vis");

  // Apply hint
  if (HINT && providerEl.querySelector('option[value="' + HINT + '"]')) {{
    providerEl.value = HINT;
    onProviderChange();
  }}

  providerEl.addEventListener("change", onProviderChange);

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
  }}

  // Toggle password visibility
  let visible = false;
  toggleBtn.addEventListener("click", function() {{
    visible = !visible;
    apiKeyEl.type = visible ? "text" : "password";
    toggleBtn.textContent = visible ? "\\u25CB" : "\\u25CF";
  }});

  // Submit
  formEl.addEventListener("submit", async function(e) {{
    e.preventDefault();
    resultEl.className = "result";
    resultEl.style.display = "none";

    const provider = providerEl.value === "_custom"
      ? customEl.value.trim()
      : providerEl.value;

    if (!provider) {{
      showError("Please select or enter a provider.");
      return;
    }}

    const value = apiKeyEl.value;
    if (!value) {{
      showError("Please enter an API key.");
      return;
    }}

    submitBtn.disabled = true;
    submitBtn.textContent = "Storing...";

    try {{
      const resp = await fetch("/register", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          provider: provider,
          env_name: envNameEl.value.trim(),
          value: value,
          description: descEl.value.trim()
        }})
      }});
      const data = await resp.json();
      if (data.ok) {{
        formEl.classList.add("form-hidden");
        resultEl.innerHTML = '<span class="check">\\u2714</span>'
          + '<strong>Stored securely</strong><br>'
          + '<span style="font-size:13px;color:#a1a1aa">'
          + provider + ' &rarr; Keychain</span>'
          + '<div class="close-hint">You can close this tab.</div>';
        resultEl.className = "result success";
      }} else {{
        showError(data.error || "Failed to store key.");
        submitBtn.disabled = false;
        submitBtn.textContent = "Store in Keychain";
      }}
    }} catch (err) {{
      showError("Connection error. Is the server running?");
      submitBtn.disabled = false;
      submitBtn.textContent = "Store in Keychain";
    }}
  }});

  function showError(msg) {{
    resultEl.textContent = msg;
    resultEl.className = "result error";
  }}
}})();
</script>
</body>
</html>"""


class _RegisterHandler(BaseHTTPRequestHandler):
    """Single-use HTTP handler for the registration popup."""

    html_content: str = ""
    keychain: KeychainStore | None = None
    on_success: object = None  # callable or None

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
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/register":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            self._json_response(400, {"ok": False, "error": "Payload too large"})
            return

        try:
            raw = self.rfile.read(length)
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"ok": False, "error": "Invalid JSON"})
            return

        provider = (data.get("provider") or "").strip()
        value = data.get("value") or ""

        if not provider:
            self._json_response(
                400, {"ok": False, "error": "Provider is required"}
            )
            return
        if not value:
            self._json_response(
                400, {"ok": False, "error": "API key is required"}
            )
            return

        try:
            provider = _validate_provider(provider)
        except ValueError as e:
            self._json_response(400, {"ok": False, "error": str(e)})
            return

        keychain = self.__class__.keychain
        if keychain is None:
            self._json_response(
                500, {"ok": False, "error": "Keychain not available"}
            )
            return

        if keychain.store(provider, value):
            self._json_response(200, {"ok": True})
            # Schedule server shutdown after response is sent
            callback = self.__class__.on_success
            if callable(callback):
                threading.Thread(
                    target=callback, daemon=True
                ).start()
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
    keychain = KeychainStore()

    # Find a free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    # Build a handler class with our state
    class Handler(_RegisterHandler):
        html_content = html

    Handler.keychain = keychain

    server = HTTPServer(("127.0.0.1", port), Handler)
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

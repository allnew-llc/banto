# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Local web UI for banto sync — serves on localhost only.

Launch with: banto sync ui
Serves at: http://localhost:8384

Uses only Python stdlib (http.server + json). No frameworks, no npm.
Secret values are NEVER sent to the browser — only metadata.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ..keychain import KeychainStore
from .config import SyncConfig
from .history import HistoryStore
from .sync import check_status

DEFAULT_PORT = 8384


def _build_status_json(config: SyncConfig) -> list[dict]:
    """Build status data for the UI. Never includes secret values."""
    entries = check_status(config)
    return [
        {
            "name": e.secret_name,
            "env_name": e.env_name,
            "keychain": e.keychain_exists,
            "targets": {k: v for k, v in e.target_status.items()},
        }
        for e in entries
    ]


def _build_history_json(config: SyncConfig) -> dict[str, list[dict]]:
    """Build version history for all secrets."""
    store = HistoryStore()
    result: dict[str, list[dict]] = {}
    for name in config.secrets:
        versions = store.list_versions(name)
        result[name] = [
            {"version": v.version, "timestamp": v.timestamp, "fingerprint": v.fingerprint}
            for v in versions
        ]
    return result


def _build_config_json(config: SyncConfig) -> dict:
    """Build config metadata for the UI."""
    secrets = []
    for name, entry in config.secrets.items():
        secrets.append({
            "name": name,
            "env_name": entry.env_name,
            "description": entry.description,
            "account": entry.account,
            "targets": [t.label for t in entry.targets],
        })
    return {
        "keychain_service": config.keychain_service,
        "environments": list(config.environments.keys()),
        "default_environment": config.default_environment,
        "secrets": secrets,
        "notifiers": [{"name": n.name, "events": n.events} for n in config.notifiers],
    }


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>banto sync</title>
<style>
:root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #e6edf3;
        --muted: #8b949e; --green: #3fb950; --red: #f85149; --blue: #58a6ff;
        --yellow: #d29922; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif;
       background: var(--bg); color: var(--text); padding: 24px; max-width: 1200px; margin: 0 auto; }
h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
.tabs { display: flex; gap: 2px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
.tab { padding: 8px 16px; cursor: pointer; color: var(--muted); border-bottom: 2px solid transparent;
       font-size: 14px; background: none; border-top: none; border-left: none; border-right: none; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--text); border-bottom-color: var(--blue); }
.panel { display: none; }
.panel.active { display: block; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; color: var(--muted); border-bottom: 1px solid var(--border);
     font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
.ok { color: var(--green); }
.fail { color: var(--red); }
.na { color: var(--muted); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px;
         font-weight: 500; }
.badge-ok { background: #0d3222; color: var(--green); }
.badge-fail { background: #3d1418; color: var(--red); }
.badge-env { background: #1c2636; color: var(--blue); }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 16px; margin-bottom: 12px; }
.card h3 { font-size: 14px; margin-bottom: 8px; }
.card p { font-size: 12px; color: var(--muted); }
.stat { display: inline-block; margin-right: 24px; }
.stat-value { font-size: 28px; font-weight: 600; }
.stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.refresh { background: var(--card); border: 1px solid var(--border); color: var(--text);
           padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.refresh:hover { border-color: var(--blue); }
.mono { font-family: 'SF Mono', 'Menlo', monospace; font-size: 12px; }
.history-version { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>banto sync</h1>
    <div class="subtitle">Local-first secret management</div>
  </div>
  <button class="refresh" onclick="refresh()">Refresh</button>
</div>

<div class="tabs">
  <button class="tab active" data-panel="status">Status</button>
  <button class="tab" data-panel="secrets">Secrets</button>
  <button class="tab" data-panel="history">History</button>
  <button class="tab" data-panel="config">Config</button>
</div>

<div id="status" class="panel active">
  <div id="stats" style="margin-bottom: 20px;"></div>
  <table><thead><tr><th>Secret</th><th>Keychain</th><th>Targets</th></tr></thead>
  <tbody id="status-body"></tbody></table>
</div>

<div id="secrets" class="panel">
  <table><thead><tr><th>Name</th><th>Env Var</th><th>Description</th><th>Targets</th></tr></thead>
  <tbody id="secrets-body"></tbody></table>
</div>

<div id="history" class="panel">
  <div id="history-body"></div>
</div>

<div id="config" class="panel">
  <div id="config-body"></div>
</div>

<script>
let data = {};

async function refresh() {
  const [status, hist, cfg] = await Promise.all([
    fetch('/api/status').then(r => r.json()),
    fetch('/api/history').then(r => r.json()),
    fetch('/api/config').then(r => r.json()),
  ]);
  data = { status, history: hist, config: cfg };
  render();
}

function render() {
  // Stats
  const total = data.status.length;
  const synced = data.status.filter(s => s.keychain && Object.values(s.targets).every(v => v !== false)).length;
  const missing = total - synced;
  document.getElementById('stats').innerHTML = `
    <span class="stat"><span class="stat-value">${total}</span><br><span class="stat-label">Secrets</span></span>
    <span class="stat"><span class="stat-value ok">${synced}</span><br><span class="stat-label">In Sync</span></span>
    ${missing ? `<span class="stat"><span class="stat-value fail">${missing}</span><br><span class="stat-label">Drifted</span></span>` : ''}
  `;

  // Status table
  let html = '';
  for (const s of data.status) {
    const kc = s.keychain ? '<span class="ok">\\u2713</span>' : '<span class="fail">\\u2717</span>';
    let targets = '';
    for (const [k, v] of Object.entries(s.targets)) {
      const cls = v === true ? 'ok' : v === false ? 'fail' : 'na';
      const sym = v === true ? '\\u2713' : v === false ? '\\u2717' : '\\u2014';
      targets += `<span class="${cls}" title="${k}">${sym} ${k.split(':').pop()}</span>&nbsp;&nbsp;`;
    }
    html += `<tr><td class="mono">${s.env_name}</td><td>${kc}</td><td>${targets}</td></tr>`;
  }
  document.getElementById('status-body').innerHTML = html;

  // Secrets table
  html = '';
  for (const s of data.config.secrets) {
    html += `<tr><td class="mono">${s.name}</td><td class="mono">${s.env_name}</td>
      <td>${s.description || '<span class="na">-</span>'}</td>
      <td>${s.targets.map(t => `<span class="badge badge-env">${t}</span> `).join('')}</td></tr>`;
  }
  document.getElementById('secrets-body').innerHTML = html;

  // History
  html = '';
  for (const [name, versions] of Object.entries(data.history)) {
    if (!versions.length) continue;
    html += `<div class="card"><h3 class="mono">${name}</h3>`;
    for (const v of [...versions].reverse()) {
      html += `<div class="history-version">v${v.version} &middot; ${v.timestamp} &middot; <span class="mono">${v.fingerprint}</span></div>`;
    }
    html += '</div>';
  }
  document.getElementById('history-body').innerHTML = html || '<p class="na">No history recorded yet.</p>';

  // Config
  const cfg = data.config;
  html = `<div class="card"><h3>Keychain Service</h3><p class="mono">${cfg.keychain_service}</p></div>`;
  if (cfg.environments.length) {
    html += `<div class="card"><h3>Environments</h3><p>${cfg.environments.map(e => `<span class="badge badge-env">${e}</span> `).join('')}</p>
      ${cfg.default_environment ? `<p style="margin-top:8px">Default: <span class="mono">${cfg.default_environment}</span></p>` : ''}</div>`;
  }
  if (cfg.notifiers.length) {
    html += `<div class="card"><h3>Notifiers</h3>`;
    for (const n of cfg.notifiers) {
      html += `<p>${n.name}: ${n.events.join(', ')}</p>`;
    }
    html += '</div>';
  }
  html += `<div class="card"><h3>Platforms</h3><p class="mono">${data.config.secrets.flatMap(s => s.targets).filter((v,i,a) => a.indexOf(v)===i).join(', ') || 'None'}</p></div>`;
  document.getElementById('config-body').innerHTML = html;
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.panel).classList.add('active');
  });
});

refresh();
</script>
</body>
</html>
"""


class SyncUIHandler(BaseHTTPRequestHandler):
    """HTTP handler for the sync local UI."""

    config: SyncConfig

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress access logs

    def do_GET(self):  # noqa: N802
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        elif self.path == "/api/status":
            self._json_response(_build_status_json(self.config))
        elif self.path == "/api/history":
            self._json_response(_build_history_json(self.config))
        elif self.path == "/api/config":
            self._json_response(_build_config_json(self.config))
        else:
            self.send_error(404)

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(config: SyncConfig, port: int = DEFAULT_PORT) -> None:
    """Start the local web UI server."""
    handler = type("Handler", (SyncUIHandler,), {"config": config})

    server = HTTPServer(("127.0.0.1", port), handler)
    url = f"http://localhost:{port}"
    print(f"banto sync UI: {url}")
    print("Press Ctrl+C to stop.\n")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

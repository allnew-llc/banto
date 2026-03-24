# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Local web UI for banto sync — serves on localhost only.

Launch with: banto sync ui
Serves at: http://localhost:8384

Uses only Python stdlib (http.server + json). No frameworks, no npm.
Provides full CRUD operations: add, delete, sync, audit, validate secrets.
Secret values are accepted via POST forms but NEVER echoed back to the browser.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from ..keychain import KeychainStore
from .config import DEFAULT_CONFIG_PATH, SecretEntry, SyncConfig, Target
from .history import HistoryStore
from .sync import check_status, sync_all, sync_secret, remove_secret

DEFAULT_PORT = 8384


def _build_status_json(config: SyncConfig) -> list[dict]:
    entries = check_status(config)
    return [
        {"name": e.secret_name, "env_name": e.env_name,
         "keychain": e.keychain_exists,
         "targets": {k: v for k, v in e.target_status.items()}}
        for e in entries
    ]


def _build_history_json(config: SyncConfig) -> dict[str, list[dict]]:
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
    secrets = []
    for name, entry in config.secrets.items():
        secrets.append({
            "name": name, "env_name": entry.env_name,
            "description": entry.description, "account": entry.account,
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
        --yellow: #d29922; --orange: #db6d28; }
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
td { padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.ok { color: var(--green); }
.fail { color: var(--red); }
.na { color: var(--muted); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500; }
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
.mono { font-family: 'SF Mono', 'Menlo', monospace; font-size: 12px; }
.history-version { color: var(--muted); font-size: 12px; }
.btn { background: var(--card); border: 1px solid var(--border); color: var(--text);
       padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.btn:hover { border-color: var(--blue); }
.btn-primary { background: #1f6feb; border-color: #1f6feb; }
.btn-primary:hover { background: #388bfd; }
.btn-danger { border-color: var(--red); color: var(--red); }
.btn-danger:hover { background: #3d1418; }
.btn-sm { padding: 3px 8px; font-size: 11px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; }
.toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 8px;
         font-size: 13px; z-index: 100; animation: fadeIn 0.2s; max-width: 400px; }
.toast-ok { background: #0d3222; color: var(--green); border: 1px solid #1a4731; }
.toast-fail { background: #3d1418; color: var(--red); border: 1px solid #5a1d23; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; } }
input, select { background: var(--bg); border: 1px solid var(--border); color: var(--text);
       padding: 6px 10px; border-radius: 6px; font-size: 13px; font-family: inherit; }
input:focus, select:focus { border-color: var(--blue); outline: none; }
.form-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.form-row label { font-size: 12px; color: var(--muted); min-width: 80px; }
.form-row input { flex: 1; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 50;
                 display: flex; align-items: center; justify-content: center; }
.modal { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
         padding: 24px; width: 480px; max-width: 90vw; }
.modal h2 { font-size: 16px; margin-bottom: 16px; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>banto sync</h1>
    <div class="subtitle">Local-first secret management</div>
  </div>
  <div style="display:flex;gap:8px;">
    <button class="btn" onclick="refresh()">Refresh</button>
  </div>
</div>

<div class="tabs">
  <button class="tab active" data-panel="status">Status</button>
  <button class="tab" data-panel="secrets">Secrets</button>
  <button class="tab" data-panel="history">History</button>
  <button class="tab" data-panel="config">Config</button>
</div>

<div id="status" class="panel active">
  <div class="toolbar">
    <div id="stats"></div>
    <div style="margin-left:auto;display:flex;gap:8px;">
      <button class="btn btn-primary" onclick="syncAll()">Sync All</button>
      <button class="btn" onclick="auditAll()">Audit</button>
    </div>
  </div>
  <table><thead><tr><th>Secret</th><th>Keychain</th><th>Targets</th><th>Actions</th></tr></thead>
  <tbody id="status-body"></tbody></table>
</div>

<div id="secrets" class="panel">
  <div class="toolbar">
    <button class="btn btn-primary" onclick="showAddModal()">+ Add Secret</button>
  </div>
  <table><thead><tr><th>Name</th><th>Env Var</th><th>Description</th><th>Targets</th><th>Actions</th></tr></thead>
  <tbody id="secrets-body"></tbody></table>
</div>

<div id="history" class="panel">
  <div id="history-body"></div>
</div>

<div id="config" class="panel">
  <div id="config-body"></div>
</div>

<div id="modal-container"></div>
<div id="toast-container"></div>

<script>
let data = {};

function toast(msg, ok=true) {
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'toast-ok' : 'toast-fail');
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function api(method, path, body=null) {
  const opts = { method };
  if (body) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json();
}

async function refresh() {
  const [status, hist, cfg] = await Promise.all([
    fetch('/api/status').then(r => r.json()),
    fetch('/api/history').then(r => r.json()),
    fetch('/api/config').then(r => r.json()),
  ]);
  data = { status, history: hist, config: cfg };
  render();
}

async function syncAll() {
  toast('Syncing all secrets...');
  const r = await api('POST', '/api/sync');
  toast(r.ok ? `Synced: ${r.ok_count} OK` : `Sync: ${r.ok_count} OK, ${r.fail_count} failed`, r.ok);
  refresh();
}

async function syncOne(name) {
  const r = await api('POST', '/api/sync', {name});
  toast(r.ok ? `${name}: synced` : `${name}: sync failed`, r.ok);
  refresh();
}

async function auditAll() {
  const r = await api('POST', '/api/audit');
  if (r.issues.length === 0) { toast('Audit: all in sync'); }
  else { toast(`Audit: ${r.issues.length} issue(s) found`, false); }
}

async function deleteSecret(name) {
  if (!confirm('Delete "' + name + '" from config, Keychain, and all targets?')) return;
  const r = await api('POST', '/api/delete', {name});
  toast(r.ok ? `Deleted: ${name}` : `Delete failed: ${name}`, r.ok);
  refresh();
}

function showAddModal() {
  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <h2>Add Secret</h2>
        <div class="form-row"><label>Name</label><input id="add-name" placeholder="openai"></div>
        <div class="form-row"><label>Env Var</label><input id="add-env" placeholder="OPENAI_API_KEY"></div>
        <div class="form-row"><label>Value</label><input id="add-value" type="password" placeholder="sk-..."></div>
        <div class="form-row"><label>Description</label><input id="add-desc" placeholder="(optional)"></div>
        <div class="form-row"><label>Target</label><input id="add-target" placeholder="cloudflare-pages:project (optional)"></div>
        <div class="modal-actions">
          <button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="doAdd()">Add</button>
        </div>
      </div>
    </div>`;
}

function closeModal() { document.getElementById('modal-container').innerHTML = ''; }

async function doAdd() {
  const name = document.getElementById('add-name').value.trim();
  const env = document.getElementById('add-env').value.trim();
  const value = document.getElementById('add-value').value;
  const desc = document.getElementById('add-desc').value.trim();
  const target = document.getElementById('add-target').value.trim();
  if (!name || !env || !value) { toast('Name, Env Var, and Value are required', false); return; }
  const r = await api('POST', '/api/add', {name, env, value, description: desc, target});
  closeModal();
  toast(r.ok ? `Added: ${name}` : `Failed: ${r.error}`, r.ok);
  refresh();
}

function showRotateModal(name) {
  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <h2>Rotate: ${name}</h2>
        <div class="form-row"><label>New Value</label><input id="rotate-value" type="password" placeholder="New secret value"></div>
        <div class="modal-actions">
          <button class="btn" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="doRotate('${name}')">Rotate & Sync</button>
        </div>
      </div>
    </div>`;
}

async function doRotate(name) {
  const value = document.getElementById('rotate-value').value;
  if (!value) { toast('Value is required', false); return; }
  const r = await api('POST', '/api/rotate', {name, value});
  closeModal();
  toast(r.ok ? `Rotated: ${name}` : `Failed: ${r.error}`, r.ok);
  refresh();
}

function render() {
  const total = data.status.length;
  const synced = data.status.filter(s => s.keychain && Object.values(s.targets).every(v => v !== false)).length;
  const missing = total - synced;
  document.getElementById('stats').innerHTML = `
    <span class="stat"><span class="stat-value">${total}</span><br><span class="stat-label">Secrets</span></span>
    <span class="stat"><span class="stat-value ok">${synced}</span><br><span class="stat-label">In Sync</span></span>
    ${missing ? `<span class="stat"><span class="stat-value fail">${missing}</span><br><span class="stat-label">Drifted</span></span>` : ''}
  `;

  let html = '';
  for (const s of data.status) {
    const kc = s.keychain ? '<span class="ok">\\u2713</span>' : '<span class="fail">\\u2717</span>';
    let targets = '';
    for (const [k, v] of Object.entries(s.targets)) {
      const cls = v === true ? 'ok' : v === false ? 'fail' : 'na';
      const sym = v === true ? '\\u2713' : v === false ? '\\u2717' : '\\u2014';
      targets += `<span class="${cls}" title="${k}">${sym} ${k.split(':').pop()}</span>&nbsp;&nbsp;`;
    }
    html += `<tr><td class="mono">${s.env_name}</td><td>${kc}</td><td>${targets || '<span class="na">no targets</span>'}</td>
      <td><button class="btn btn-sm" onclick="syncOne('${s.name}')">Sync</button>
          <button class="btn btn-sm" onclick="showRotateModal('${s.name}')">Rotate</button></td></tr>`;
  }
  document.getElementById('status-body').innerHTML = html || '<tr><td colspan="4" class="na">No secrets configured</td></tr>';

  html = '';
  for (const s of data.config.secrets) {
    html += `<tr><td class="mono">${s.name}</td><td class="mono">${s.env_name}</td>
      <td>${s.description || '<span class="na">-</span>'}</td>
      <td>${s.targets.map(t => `<span class="badge badge-env">${t}</span> `).join('') || '<span class="na">-</span>'}</td>
      <td><button class="btn btn-sm btn-danger" onclick="deleteSecret('${s.name}')">Delete</button></td></tr>`;
  }
  document.getElementById('secrets-body').innerHTML = html || '<tr><td colspan="5" class="na">No secrets. Click "+ Add Secret" to get started.</td></tr>';

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

  const cfg = data.config;
  html = `<div class="card"><h3>Keychain Service</h3><p class="mono">${cfg.keychain_service}</p></div>`;
  if (cfg.environments.length) {
    html += `<div class="card"><h3>Environments</h3><p>${cfg.environments.map(e => `<span class="badge badge-env">${e}</span> `).join('')}</p>
      ${cfg.default_environment ? `<p style="margin-top:8px">Default: <span class="mono">${cfg.default_environment}</span></p>` : ''}</div>`;
  }
  if (cfg.notifiers.length) {
    html += `<div class="card"><h3>Notifiers</h3>`;
    for (const n of cfg.notifiers) html += `<p>${n.name}: ${n.events.join(', ')}</p>`;
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
    config: SyncConfig
    config_path: Path

    def log_message(self, format, *args):  # noqa: A002
        pass

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

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/sync":
            self._handle_sync(body)
        elif self.path == "/api/add":
            self._handle_add(body)
        elif self.path == "/api/delete":
            self._handle_delete(body)
        elif self.path == "/api/rotate":
            self._handle_rotate(body)
        elif self.path == "/api/audit":
            self._handle_audit()
        else:
            self.send_error(404)

    def _handle_sync(self, body: dict) -> None:
        name = body.get("name")
        if name:
            report = sync_secret(self.config, name)
        else:
            report = sync_all(self.config)
        self._json_response({
            "ok": report.all_ok,
            "ok_count": report.ok_count,
            "fail_count": report.fail_count,
            "results": [{"name": r.secret_name, "target": r.target_label,
                         "success": r.success, "message": r.message}
                        for r in report.results],
        })

    def _handle_add(self, body: dict) -> None:
        name = body.get("name", "").strip()
        env = body.get("env", "").strip()
        value = body.get("value", "")
        desc = body.get("description", "")
        target_str = body.get("target", "").strip()

        if not name or not env or not value:
            self._json_response({"ok": False, "error": "name, env, and value are required"})
            return

        if self.config.get_secret(name):
            self._json_response({"ok": False, "error": f"'{name}' already exists"})
            return

        kc = KeychainStore(service_prefix=self.config.keychain_service)
        if not kc.store(name, value):
            self._json_response({"ok": False, "error": "Failed to store in Keychain"})
            return

        targets: list[Target] = []
        if target_str and ":" in target_str:
            platform, project = target_str.split(":", 1)
            if platform == "local":
                targets.append(Target(platform="local", file=project))
            else:
                targets.append(Target(platform=platform, project=project))

        entry = SecretEntry(name=name, account=name, env_name=env,
                            description=desc, targets=targets)
        self.config.add_secret(entry)
        self.config.save(self.config_path)

        history = HistoryStore()
        history.record(name, value, self.config.keychain_service)

        self._json_response({"ok": True})

    def _handle_delete(self, body: dict) -> None:
        name = body.get("name", "")
        if not name:
            self._json_response({"ok": False, "error": "name required"})
            return
        report = remove_secret(self.config, name)
        self.config.save(self.config_path)
        self._json_response({"ok": True, "results": report.ok_count})

    def _handle_rotate(self, body: dict) -> None:
        name = body.get("name", "")
        value = body.get("value", "")
        if not name or not value:
            self._json_response({"ok": False, "error": "name and value required"})
            return

        entry = self.config.get_secret(name)
        if not entry:
            self._json_response({"ok": False, "error": f"'{name}' not found"})
            return

        kc = KeychainStore(service_prefix=self.config.keychain_service)
        if not kc.store(entry.account, value):
            self._json_response({"ok": False, "error": "Keychain update failed"})
            return

        history = HistoryStore()
        history.record(name, value, self.config.keychain_service)

        if entry.targets:
            report = sync_secret(self.config, name)
            self._json_response({
                "ok": report.all_ok,
                "ok_count": report.ok_count,
                "fail_count": report.fail_count,
            })
        else:
            self._json_response({"ok": True})

    def _handle_audit(self) -> None:
        entries = check_status(self.config)
        issues: list[str] = []
        for entry in entries:
            if not entry.keychain_exists:
                issues.append(f"{entry.env_name}: missing in Keychain")
            for label, status in entry.target_status.items():
                if status is False:
                    issues.append(f"{entry.env_name} -> {label}")
        self._json_response({"ok": len(issues) == 0, "issues": issues})

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(config: SyncConfig, port: int = DEFAULT_PORT,
          config_path: Path | None = None) -> None:
    """Start the local web UI server."""
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    handler = type("Handler", (SyncUIHandler,),
                   {"config": config, "config_path": cfg_path})

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

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Local web UI for banto sync — serves on localhost only.

Launch with: banto sync ui
Serves at: http://localhost:8384

Uses only Python stdlib (http.server + json). No frameworks, no npm.
Provides full CRUD operations: add, edit, delete, sync, audit, validate,
export secrets. Secret values are accepted via POST forms but NEVER echoed
back to the browser.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..keychain import KeychainStore
from .config import DEFAULT_CONFIG_PATH, SecretEntry, SyncConfig, Target
from .drivers import DRIVER_MAP
from .history import HistoryStore
from .sync import check_status, sync_all, sync_secret, remove_secret

DEFAULT_PORT = 8384

ALL_PLATFORMS = sorted(DRIVER_MAP.keys())


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
            "targets": [t.to_dict() for t in entry.targets],
            "target_labels": [t.label for t in entry.targets],
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
:root {
  --bg: #0d1117; --bg2: #010409; --card: #161b22; --card-hover: #1c2333;
  --border: #30363d; --border-active: #58a6ff;
  --text: #e6edf3; --text2: #c9d1d9; --muted: #8b949e; --subtle: #484f58;
  --green: #3fb950; --green-bg: #0d3222; --green-border: #1a4731;
  --red: #f85149; --red-bg: #3d1418; --red-border: #5a1d23;
  --blue: #58a6ff; --blue-bg: #1c2636; --blue-border: #1f3a5f;
  --yellow: #d29922; --yellow-bg: #2d2300; --yellow-border: #4b3800;
  --orange: #db6d28; --purple: #bc8cff; --purple-bg: #1e1533;
  --radius: 8px; --radius-sm: 6px; --radius-lg: 12px;
  --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 2px 8px rgba(0,0,0,0.2);
  --shadow-lg: 0 8px 30px rgba(0,0,0,0.4);
  --transition: 150ms cubic-bezier(0.4, 0, 0.2, 1);
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
  background: var(--bg2); color: var(--text); line-height: 1.5; -webkit-font-smoothing: antialiased;
}
.app-shell { max-width: 1280px; margin: 0 auto; padding: 24px 32px; }

/* Header */
.app-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 28px; }
.app-header h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.3px; display: flex; align-items: center; gap: 10px; }
.app-header h1 .logo { width: 28px; height: 28px; background: linear-gradient(135deg, var(--blue), var(--purple)); border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; color: #fff; }
.subtitle { color: var(--muted); font-size: 13px; margin-top: 2px; }
.header-actions { display: flex; gap: 8px; align-items: center; }

/* Tabs */
.tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
.tab {
  padding: 10px 18px; cursor: pointer; color: var(--muted); font-size: 14px; font-weight: 500;
  background: none; border: none; border-bottom: 2px solid transparent; transition: all var(--transition);
  position: relative; white-space: nowrap;
}
.tab:hover { color: var(--text2); }
.tab.active { color: var(--text); border-bottom-color: var(--blue); }
.tab .tab-count {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px; margin-left: 6px;
  font-size: 11px; font-weight: 500; border-radius: 9px;
  background: var(--border); color: var(--muted);
}
.tab.active .tab-count { background: var(--blue-bg); color: var(--blue); }

/* Panels */
.panel { display: none; }
.panel.active { display: block; animation: fadeUp 0.2s ease; }
@keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

/* Stats bar */
.stats-bar {
  display: flex; gap: 16px; margin-bottom: 20px; padding: 16px 20px;
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg);
}
.stat-item { display: flex; flex-direction: column; }
.stat-value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
.stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; font-weight: 500; margin-top: 2px; }
.stat-divider { width: 1px; background: var(--border); margin: 0 4px; }

/* Toolbar */
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.toolbar-spacer { flex: 1; }

/* Search */
.search-box {
  position: relative; flex: 0 1 280px;
}
.search-box input {
  width: 100%; padding: 7px 12px 7px 34px;
  background: var(--bg); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--radius-sm); font-size: 13px; transition: border-color var(--transition);
}
.search-box input:focus { border-color: var(--blue); outline: none; }
.search-box .search-icon {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: var(--muted); font-size: 14px; pointer-events: none;
}

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead { position: sticky; top: 0; z-index: 2; }
th {
  text-align: left; padding: 10px 14px; color: var(--muted); font-weight: 500;
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
  background: var(--card); border-bottom: 1px solid var(--border);
}
td {
  padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: middle;
  transition: background var(--transition);
}
tr:hover td { background: rgba(22, 27, 34, 0.5); }
.table-wrapper {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  overflow: hidden;
}
.table-wrapper table { margin: 0; }
.table-wrapper th:first-child, .table-wrapper td:first-child { padding-left: 20px; }
.table-wrapper th:last-child, .table-wrapper td:last-child { padding-right: 20px; }
.empty-state {
  padding: 48px 24px; text-align: center; color: var(--muted);
}
.empty-state .empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.4; }
.empty-state h3 { font-size: 16px; color: var(--text2); margin-bottom: 4px; }
.empty-state p { font-size: 13px; }

/* Status indicators */
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.status-dot-ok { background: var(--green); box-shadow: 0 0 6px rgba(63, 185, 80, 0.4); }
.status-dot-fail { background: var(--red); box-shadow: 0 0 6px rgba(248, 81, 73, 0.4); }
.status-dot-na { background: var(--subtle); }

/* Badges */
.badge {
  display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
  border-radius: 12px; font-size: 11px; font-weight: 500; white-space: nowrap;
  cursor: default; transition: all var(--transition); border: 1px solid transparent;
}
.badge-ok { background: var(--green-bg); color: var(--green); border-color: var(--green-border); }
.badge-fail { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }
.badge-env { background: var(--blue-bg); color: var(--blue); border-color: var(--blue-border); }
.badge-warn { background: var(--yellow-bg); color: var(--yellow); border-color: var(--yellow-border); }
.badge-platform {
  background: var(--purple-bg); color: var(--purple); border-color: rgba(188,140,255,0.2);
  cursor: pointer;
}
.badge-platform:hover { border-color: var(--purple); }
.badges-wrap { display: flex; flex-wrap: wrap; gap: 4px; }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 14px; border-radius: var(--radius-sm); cursor: pointer;
  font-size: 13px; font-weight: 500; font-family: inherit;
  border: 1px solid var(--border); background: var(--card); color: var(--text);
  transition: all var(--transition); white-space: nowrap;
}
.btn:hover { border-color: var(--muted); background: var(--card-hover); }
.btn:active { transform: scale(0.98); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.btn-primary:hover { background: #388bfd; border-color: #388bfd; }
.btn-danger { border-color: var(--red-border); color: var(--red); }
.btn-danger:hover { background: var(--red-bg); border-color: var(--red); }
.btn-ghost { background: transparent; border-color: transparent; }
.btn-ghost:hover { background: var(--card); border-color: var(--border); }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-icon { padding: 4px 8px; font-size: 14px; line-height: 1; }
.btn-group { display: inline-flex; gap: 0; }
.btn-group .btn { border-radius: 0; margin-left: -1px; }
.btn-group .btn:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); margin-left: 0; }
.btn-group .btn:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }

/* Cards */
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 20px; margin-bottom: 12px; transition: border-color var(--transition);
}
.card:hover { border-color: var(--subtle); }
.card h3 { font-size: 14px; font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.card p { font-size: 13px; color: var(--muted); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }

/* Mono */
.mono { font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Menlo', 'Consolas', monospace; font-size: 12px; }

/* History */
.history-card { cursor: pointer; }
.history-card .expand-icon { margin-left: auto; color: var(--muted); transition: transform var(--transition); }
.history-card.open .expand-icon { transform: rotate(180deg); }
.history-versions { display: none; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.history-card.open .history-versions { display: block; }
.history-version {
  display: flex; align-items: center; gap: 12px; padding: 6px 0;
  font-size: 12px; color: var(--muted);
}
.history-version .ver-num { color: var(--text); font-weight: 500; min-width: 36px; }
.history-version .ver-fp { font-family: 'SF Mono', monospace; color: var(--subtle); }

/* Collapsible audit */
.audit-panel {
  margin-top: 16px; background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); overflow: hidden; display: none;
}
.audit-panel.open { display: block; animation: fadeUp 0.2s ease; }
.audit-panel-header {
  padding: 12px 20px; font-size: 13px; font-weight: 600;
  display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid var(--border); background: rgba(22,27,34,0.6);
}
.audit-panel-body { padding: 16px 20px; max-height: 300px; overflow-y: auto; }
.audit-issue {
  display: flex; align-items: center; gap: 8px; padding: 6px 0;
  font-size: 13px; border-bottom: 1px solid var(--border);
}
.audit-issue:last-child { border-bottom: none; }

/* Config sections */
.config-section { margin-bottom: 24px; }
.config-section h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.config-kv { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.config-kv:last-child { border-bottom: none; }
.config-kv dt { color: var(--muted); min-width: 140px; font-weight: 500; }
.config-kv dd { color: var(--text); }

/* Export */
.export-container { display: grid; grid-template-columns: 280px 1fr; gap: 20px; }
.export-controls { display: flex; flex-direction: column; gap: 16px; }
.export-preview {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px 20px; font-family: 'SF Mono', monospace; font-size: 12px;
  line-height: 1.6; white-space: pre-wrap; word-break: break-all;
  max-height: 500px; overflow-y: auto; color: var(--text2); position: relative;
}
.export-preview .copy-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(13,17,23,0.8); color: var(--green); font-size: 14px; font-weight: 600;
  opacity: 0; transition: opacity 0.2s; pointer-events: none;
}
.export-preview .copy-overlay.show { opacity: 1; }

/* Form elements */
.form-group { margin-bottom: 14px; }
.form-label { display: block; font-size: 12px; color: var(--muted); font-weight: 500; margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.4px; }
input[type="text"], input[type="password"], select, textarea {
  width: 100%; padding: 8px 12px;
  background: var(--bg); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--radius-sm); font-size: 13px; font-family: inherit;
  transition: border-color var(--transition);
}
input:focus, select:focus, textarea:focus { border-color: var(--blue); outline: none; }
select { cursor: pointer; -webkit-appearance: none; appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%238b949e' d='M2 4l4 4 4-4'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px;
}
.input-with-action { display: flex; gap: 6px; }
.input-with-action input, .input-with-action select { flex: 1; }
.password-wrapper { position: relative; }
.password-wrapper input { padding-right: 40px; }
.password-toggle {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px;
  padding: 4px; line-height: 1;
}
.password-toggle:hover { color: var(--text); }

/* Target rows */
.target-rows { display: flex; flex-direction: column; gap: 8px; }
.target-row { display: flex; gap: 8px; align-items: center; animation: fadeUp 0.15s ease; }
.target-row select { flex: 0 0 220px; }
.target-row input { flex: 1; }
.target-row .btn-remove { flex: 0 0 auto; color: var(--red); background: transparent; border: none; cursor: pointer; font-size: 16px; padding: 4px 8px; border-radius: 4px; transition: background var(--transition); }
.target-row .btn-remove:hover { background: var(--red-bg); }

/* Modal */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.65); backdrop-filter: blur(4px);
  z-index: 100; display: flex; align-items: center; justify-content: center;
  animation: fadeIn 0.15s ease;
}
.modal {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 0; width: 560px; max-width: 92vw; max-height: 90vh; overflow-y: auto;
  box-shadow: var(--shadow-lg); animation: modalIn 0.2s ease;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 20px 24px 0; margin-bottom: 20px;
}
.modal-header h2 { font-size: 18px; font-weight: 600; }
.modal-close { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 20px; padding: 4px 8px; border-radius: 4px; line-height: 1; }
.modal-close:hover { color: var(--text); background: rgba(255,255,255,0.05); }
.modal-body { padding: 0 24px; }
.modal-footer { display: flex; gap: 8px; justify-content: flex-end; padding: 20px 24px; border-top: 1px solid var(--border); margin-top: 20px; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes modalIn { from { opacity: 0; transform: scale(0.96) translateY(8px); } to { opacity: 1; transform: scale(1) translateY(0); } }

/* Confirm dialog */
.confirm-dialog { width: 420px; }
.confirm-dialog .modal-body { padding: 0 24px; font-size: 14px; color: var(--text2); }
.confirm-dialog .warn-text { color: var(--red); font-weight: 500; }

/* Toast */
.toast-container { position: fixed; top: 20px; right: 20px; z-index: 200; display: flex; flex-direction: column; gap: 8px; }
.toast {
  padding: 12px 20px; border-radius: var(--radius); font-size: 13px; font-weight: 500;
  max-width: 420px; display: flex; align-items: center; gap: 10px;
  box-shadow: var(--shadow-lg); animation: toastIn 0.25s ease; pointer-events: auto;
}
.toast-ok { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }
.toast-fail { background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }
.toast-info { background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue-border); }
.toast-icon { font-size: 16px; flex-shrink: 0; }
.toast-dismiss { margin-left: auto; background: none; border: none; color: inherit; cursor: pointer; opacity: 0.6; font-size: 14px; }
.toast-dismiss:hover { opacity: 1; }
@keyframes toastIn { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: translateX(0); } }
@keyframes toastOut { from { opacity: 1; transform: translateX(0); } to { opacity: 0; transform: translateX(20px); } }

/* Spinner overlay */
.spinner-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 150;
  display: none; align-items: center; justify-content: center;
}
.spinner-overlay.active { display: flex; }
.spinner {
  width: 36px; height: 36px; border: 3px solid var(--border); border-top-color: var(--blue);
  border-radius: 50%; animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Responsive */
@media (max-width: 768px) {
  .app-shell { padding: 16px; }
  .stats-bar { flex-wrap: wrap; gap: 12px; }
  .stat-divider { display: none; }
  .export-container { grid-template-columns: 1fr; }
  .search-box { flex: 1 1 200px; }
  .target-row select { flex: 0 0 160px; }
}
</style>
</head>
<body>
<div class="app-shell">

<div class="app-header">
  <div>
    <h1><span class="logo">B</span> banto sync</h1>
    <div class="subtitle">Local-first secret management &mdash; Keychain to 33+ platforms</div>
  </div>
  <div class="header-actions">
    <button class="btn btn-ghost btn-sm" onclick="refresh()" title="Refresh data">&#x21bb; Refresh</button>
  </div>
</div>

<div class="tabs" id="tab-bar">
  <button class="tab active" data-panel="status">Status <span class="tab-count" id="tc-status">0</span></button>
  <button class="tab" data-panel="secrets">Secrets <span class="tab-count" id="tc-secrets">0</span></button>
  <button class="tab" data-panel="history">History</button>
  <button class="tab" data-panel="config">Config</button>
  <button class="tab" data-panel="export">Export</button>
</div>

<!-- ==================== STATUS TAB ==================== -->
<div id="status" class="panel active">
  <div id="stats-bar" class="stats-bar"></div>
  <div class="toolbar">
    <div class="btn-group">
      <button class="btn btn-primary btn-sm" onclick="syncAll()">&#x2191; Sync All</button>
      <button class="btn btn-sm" onclick="auditAll()">&#x1f50d; Audit</button>
      <button class="btn btn-sm" onclick="validateAll()">&#x2713; Validate</button>
      <button class="btn btn-sm" onclick="validateKeychain()">&#x1F511; Validate Keychain</button>
    </div>
    <div class="toolbar-spacer"></div>
  </div>
  <div class="table-wrapper">
    <table><thead><tr>
      <th style="width:28%">Secret</th><th style="width:10%">Keychain</th>
      <th>Targets</th><th style="width:180px">Actions</th>
    </tr></thead>
    <tbody id="status-body"></tbody></table>
  </div>
  <div id="audit-panel" class="audit-panel"></div>
</div>

<!-- ==================== SECRETS TAB ==================== -->
<div id="secrets" class="panel">
  <div class="toolbar">
    <button class="btn btn-primary btn-sm" onclick="showAddModal()">+ Add Secret</button>
    <div class="toolbar-spacer"></div>
    <div class="search-box">
      <span class="search-icon">&#x1f50e;</span>
      <input type="text" id="secrets-search" placeholder="Filter secrets..." oninput="renderSecrets()">
    </div>
  </div>
  <div class="table-wrapper">
    <table><thead><tr>
      <th>Name</th><th>Env Var</th><th>Description</th><th>Targets</th><th style="width:140px">Actions</th>
    </tr></thead>
    <tbody id="secrets-body"></tbody></table>
  </div>
</div>

<!-- ==================== HISTORY TAB ==================== -->
<div id="history" class="panel">
  <div id="history-body"></div>
</div>

<!-- ==================== CONFIG TAB ==================== -->
<div id="config" class="panel">
  <div id="config-body"></div>
</div>

<!-- ==================== EXPORT TAB ==================== -->
<div id="export" class="panel">
  <div class="export-container">
    <div class="export-controls">
      <div class="card" style="margin-bottom:0">
        <h3>Export Settings</h3>
        <div class="form-group">
          <label class="form-label">Format</label>
          <select id="export-format" onchange="updateExportPreview()">
            <option value="env">.env (dotenv)</option>
            <option value="json">JSON</option>
            <option value="docker">Docker env-file</option>
          </select>
        </div>
        <div class="form-group" id="export-env-group" style="display:none">
          <label class="form-label">Environment</label>
          <select id="export-env" onchange="updateExportPreview()">
            <option value="">(default)</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;">
          <button class="btn btn-primary btn-sm" onclick="copyExport()">&#x1f4cb; Copy to Clipboard</button>
        </div>
      </div>
    </div>
    <div style="position:relative;">
      <div class="export-preview" id="export-preview">
        <div class="copy-overlay" id="copy-overlay">Copied!</div>
        <span class="muted">Loading...</span>
      </div>
    </div>
  </div>
</div>

<!-- Containers -->
<div id="modal-container"></div>
<div class="toast-container" id="toast-container"></div>
<div class="spinner-overlay" id="spinner"><div class="spinner"></div></div>

</div><!-- /app-shell -->

<script>
/* ===================== STATE ===================== */
let data = { status: [], history: {}, config: { secrets: [], environments: [], notifiers: [], keychain_service: '' }, drivers: [] };
let auditResults = null;

const PLATFORMS = PLATFORM_LIST_PLACEHOLDER;

const ENV_PRESETS = [
  { label: 'Custom', value: '' },
  { label: 'OPENAI_API_KEY', value: 'OPENAI_API_KEY' },
  { label: 'ANTHROPIC_API_KEY', value: 'ANTHROPIC_API_KEY' },
  { label: 'GEMINI_API_KEY', value: 'GEMINI_API_KEY' },
  { label: 'GITHUB_TOKEN', value: 'GITHUB_TOKEN' },
  { label: 'CLOUDFLARE_API_TOKEN', value: 'CLOUDFLARE_API_TOKEN' },
  { label: 'AWS_ACCESS_KEY_ID', value: 'AWS_ACCESS_KEY_ID' },
  { label: 'AWS_SECRET_ACCESS_KEY', value: 'AWS_SECRET_ACCESS_KEY' },
  { label: 'DATABASE_URL', value: 'DATABASE_URL' },
  { label: 'STRIPE_SECRET_KEY', value: 'STRIPE_SECRET_KEY' },
  { label: 'SENDGRID_API_KEY', value: 'SENDGRID_API_KEY' },
];

/* ===================== UTILITIES ===================== */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function toast(msg, type) {
  type = type || 'ok';
  const icons = { ok: '\\u2713', fail: '\\u2717', info: '\\u2139' };
  const el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.innerHTML = '<span class="toast-icon">' + (icons[type] || '') + '</span>' +
    '<span>' + esc(msg) + '</span>' +
    '<button class="toast-dismiss" onclick="this.parentElement.remove()">\\u2715</button>';
  document.getElementById('toast-container').appendChild(el);
  setTimeout(function() {
    el.style.animation = 'toastOut 0.2s ease forwards';
    setTimeout(function() { el.remove(); }, 200);
  }, 5000);
}

function showSpinner() { document.getElementById('spinner').classList.add('active'); }
function hideSpinner() { document.getElementById('spinner').classList.remove('active'); }

async function api(method, path, body) {
  showSpinner();
  try {
    var opts = { method: method };
    if (body !== undefined && body !== null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    var r = await fetch(path, opts);
    return await r.json();
  } catch (e) {
    toast('Network error: ' + e.message, 'fail');
    return { ok: false, error: e.message };
  } finally {
    hideSpinner();
  }
}

function closeModal() {
  document.getElementById('modal-container').innerHTML = '';
}

function onModalOverlayClick(ev) {
  if (ev.target.classList.contains('modal-overlay')) closeModal();
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeModal();
});

/* ===================== DATA LOADING ===================== */
async function refresh() {
  showSpinner();
  try {
    var [status, hist, cfg, drv] = await Promise.all([
      fetch('/api/status').then(function(r) { return r.json(); }),
      fetch('/api/history').then(function(r) { return r.json(); }),
      fetch('/api/config').then(function(r) { return r.json(); }),
      fetch('/api/drivers').then(function(r) { return r.json(); }),
    ]);
    data = { status: status, history: hist, config: cfg, drivers: drv.drivers || [] };
    render();
  } catch (e) {
    toast('Failed to load data', 'fail');
  } finally {
    hideSpinner();
  }
}

/* ===================== ACTIONS ===================== */
async function syncAll() {
  var r = await api('POST', '/api/sync');
  if (r.ok) toast('Synced: ' + r.ok_count + ' OK');
  else toast('Sync: ' + r.ok_count + ' OK, ' + r.fail_count + ' failed', 'fail');
  refresh();
}

async function syncOne(name) {
  var r = await api('POST', '/api/sync', { name: name });
  if (r.ok) toast(name + ': synced');
  else toast(name + ': sync failed', 'fail');
  refresh();
}

async function auditAll() {
  var r = await api('POST', '/api/audit');
  auditResults = r;
  if (r.issues && r.issues.length === 0) {
    toast('Audit passed: all secrets in sync');
    renderAuditPanel([]);
  } else {
    toast('Audit: ' + r.issues.length + ' issue(s) found', 'fail');
    renderAuditPanel(r.issues);
  }
}

async function validateAll() {
  var r = await api('POST', '/api/validate');
  if (!r.results || r.results.length === 0) {
    toast('No secrets to validate', 'info');
    return;
  }
  showValidateResults('Sync Config Keys', r.results);
}

async function validateKeychain() {
  toast('Scanning Keychain for known API keys...');
  var r = await api('POST', '/api/validate-keychain');
  if (!r.results || r.results.length === 0) {
    toast('No known API keys found in Keychain', 'info');
    return;
  }
  showValidateResults('Keychain Keys', r.results);
}

function showValidateResults(title, results) {
  var valid = results.filter(function(x) { return x.valid; }).length;
  var invalid = results.filter(function(x) { return !x.valid; }).length;
  if (invalid === 0) toast('Validate: all ' + valid + ' keys valid');
  else toast('Validate: ' + valid + ' valid, ' + invalid + ' invalid', 'fail');

  var html = '<div class="card" style="margin-top:12px;">';
  html += '<h3>' + esc(title) + ' — Validation Results</h3>';
  html += '<table><thead><tr><th>Key</th><th>Provider</th><th>Status</th><th>Details</th></tr></thead><tbody>';
  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    var cls = r.valid ? 'ok' : 'fail';
    var badge = r.valid ? '<span class="badge badge-ok">PASS</span>' : '<span class="badge badge-fail">FAIL</span>';
    html += '<tr><td class="mono">' + esc(r.name) + '</td><td>' + esc(r.provider) + '</td><td>' + badge + '</td><td class="na">' + esc(r.message) + '</td></tr>';
  }
  html += '</tbody></table></div>';

  var panel = document.getElementById('validate-results');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'validate-results';
    document.getElementById('status').appendChild(panel);
  }
  panel.innerHTML = html;
}

async function deleteSecret(name) {
  showConfirmDialog(
    'Delete Secret',
    'This will permanently remove <strong>' + esc(name) + '</strong> from your config, Keychain, and all deployment targets. This action cannot be undone.',
    'Delete',
    async function() {
      var r = await api('POST', '/api/delete', { name: name });
      if (r.ok) toast('Deleted: ' + name);
      else toast('Delete failed: ' + name, 'fail');
      refresh();
    }
  );
}

function showConfirmDialog(title, bodyHtml, actionLabel, onConfirm) {
  document.getElementById('modal-container').innerHTML =
    '<div class="modal-overlay" onclick="onModalOverlayClick(event)">' +
    '<div class="modal confirm-dialog">' +
    '<div class="modal-header"><h2>' + esc(title) + '</h2>' +
    '<button class="modal-close" onclick="closeModal()">\\u2715</button></div>' +
    '<div class="modal-body">' + bodyHtml + '</div>' +
    '<div class="modal-footer">' +
    '<button class="btn btn-sm" onclick="closeModal()">Cancel</button>' +
    '<button class="btn btn-danger btn-sm" id="confirm-action-btn">' + esc(actionLabel) + '</button>' +
    '</div></div></div>';
  document.getElementById('confirm-action-btn').addEventListener('click', function() {
    closeModal();
    onConfirm();
  });
}

/* ===================== ROTATE MODAL ===================== */
function showRotateModal(name) {
  document.getElementById('modal-container').innerHTML =
    '<div class="modal-overlay" onclick="onModalOverlayClick(event)">' +
    '<div class="modal">' +
    '<div class="modal-header"><h2>Rotate: ' + esc(name) + '</h2>' +
    '<button class="modal-close" onclick="closeModal()">\\u2715</button></div>' +
    '<div class="modal-body">' +
    '<div class="form-group"><label class="form-label">New Value</label>' +
    '<div class="password-wrapper"><input type="password" id="rotate-value" placeholder="Enter new secret value...">' +
    '<button class="password-toggle" onclick="togglePw(this)" type="button" title="Show/hide">\\u25cf</button></div></div>' +
    '</div>' +
    '<div class="modal-footer">' +
    '<button class="btn btn-sm" onclick="closeModal()">Cancel</button>' +
    '<button class="btn btn-primary btn-sm" onclick="doRotate(\\''+esc(name)+'\\')">Rotate &amp; Sync</button>' +
    '</div></div></div>';
  document.getElementById('rotate-value').focus();
}

async function doRotate(name) {
  var value = document.getElementById('rotate-value').value;
  if (!value) { toast('Value is required', 'fail'); return; }
  var r = await api('POST', '/api/rotate', { name: name, value: value });
  closeModal();
  if (r.ok) toast('Rotated: ' + name);
  else toast('Failed: ' + (r.error || 'unknown'), 'fail');
  refresh();
}

function togglePw(btn) {
  var input = btn.parentElement.querySelector('input');
  if (input.type === 'password') { input.type = 'text'; btn.textContent = '\\u25cb'; }
  else { input.type = 'password'; btn.textContent = '\\u25cf'; }
}

/* ===================== ADD SECRET MODAL ===================== */
function buildPlatformOptions() {
  var opts = '<option value="">Select platform...</option>';
  var platforms = data.drivers && data.drivers.length ? data.drivers : PLATFORMS;
  for (var i = 0; i < platforms.length; i++) {
    opts += '<option value="' + esc(platforms[i]) + '">' + esc(platforms[i]) + '</option>';
  }
  return opts;
}

function buildEnvPresetOptions() {
  var opts = '';
  for (var i = 0; i < ENV_PRESETS.length; i++) {
    opts += '<option value="' + esc(ENV_PRESETS[i].value) + '">' + esc(ENV_PRESETS[i].label) + '</option>';
  }
  return opts;
}

var addTargetCounter = 0;

function addTargetRow(containerId, platform, project) {
  addTargetCounter++;
  var id = 'tgt-' + addTargetCounter;
  var container = document.getElementById(containerId);
  var row = document.createElement('div');
  row.className = 'target-row';
  row.id = id;
  row.innerHTML =
    '<select onchange="onTargetPlatformChange(this)">' + buildPlatformOptions() + '</select>' +
    '<input type="text" placeholder="Project name or file path" value="' + esc(project || '') + '">' +
    '<button class="btn-remove" onclick="document.getElementById(\\''+id+'\\').remove()" title="Remove target">\\u2715</button>';
  if (platform) row.querySelector('select').value = platform;
  container.appendChild(row);
}

function onTargetPlatformChange(sel) {
  var input = sel.parentElement.querySelector('input[type=text]');
  if (sel.value === 'local') input.placeholder = 'File path (e.g. /path/to/.env)';
  else input.placeholder = 'Project name';
}

function collectTargets(containerId) {
  var container = document.getElementById(containerId);
  var rows = container.querySelectorAll('.target-row');
  var targets = [];
  for (var i = 0; i < rows.length; i++) {
    var platform = rows[i].querySelector('select').value;
    var val = rows[i].querySelector('input[type=text]').value.trim();
    if (!platform) continue;
    if (platform === 'local') targets.push({ platform: platform, file: val });
    else targets.push({ platform: platform, project: val });
  }
  return targets;
}

function showAddModal() {
  addTargetCounter = 0;
  document.getElementById('modal-container').innerHTML =
    '<div class="modal-overlay" onclick="onModalOverlayClick(event)">' +
    '<div class="modal">' +
    '<div class="modal-header"><h2>Add Secret</h2>' +
    '<button class="modal-close" onclick="closeModal()">\\u2715</button></div>' +
    '<div class="modal-body">' +
    '<div class="form-group"><label class="form-label">Name</label>' +
    '<input type="text" id="add-name" placeholder="e.g. openai"></div>' +
    '<div class="form-group"><label class="form-label">Env Var</label>' +
    '<div class="input-with-action">' +
    '<input type="text" id="add-env" placeholder="OPENAI_API_KEY">' +
    '<select id="add-env-preset" onchange="onEnvPresetChange()" style="flex:0 0 180px">' +
    buildEnvPresetOptions() + '</select></div></div>' +
    '<div class="form-group"><label class="form-label">Value</label>' +
    '<div class="password-wrapper"><input type="password" id="add-value" placeholder="sk-...">' +
    '<button class="password-toggle" onclick="togglePw(this)" type="button" title="Show/hide">\\u25cf</button></div></div>' +
    '<div class="form-group"><label class="form-label">Description</label>' +
    '<input type="text" id="add-desc" placeholder="Optional description"></div>' +
    '<div class="form-group"><label class="form-label">Targets</label>' +
    '<div class="target-rows" id="add-targets"></div>' +
    '<button class="btn btn-ghost btn-sm" onclick="addTargetRow(\\'add-targets\\',\\'\\',\\'\\')" style="margin-top:8px">+ Add Target</button></div>' +
    '</div>' +
    '<div class="modal-footer">' +
    '<button class="btn btn-sm" onclick="closeModal()">Cancel</button>' +
    '<button class="btn btn-primary btn-sm" onclick="doAdd()">Add Secret</button>' +
    '</div></div></div>';
  document.getElementById('add-name').focus();
}

function onEnvPresetChange() {
  var preset = document.getElementById('add-env-preset').value;
  if (preset) document.getElementById('add-env').value = preset;
}

async function doAdd() {
  var name = document.getElementById('add-name').value.trim();
  var env = document.getElementById('add-env').value.trim();
  var value = document.getElementById('add-value').value;
  var desc = document.getElementById('add-desc').value.trim();
  var targets = collectTargets('add-targets');
  if (!name || !env || !value) { toast('Name, Env Var, and Value are required', 'fail'); return; }
  var r = await api('POST', '/api/add', { name: name, env: env, value: value, description: desc, targets: targets });
  closeModal();
  if (r.ok) toast('Added: ' + name);
  else toast('Failed: ' + (r.error || 'unknown'), 'fail');
  refresh();
}

/* ===================== EDIT SECRET MODAL ===================== */
function showEditModal(name) {
  addTargetCounter = 0;
  var secret = data.config.secrets.find(function(s) { return s.name === name; });
  if (!secret) { toast('Secret not found', 'fail'); return; }
  var editEnvPresetOpts = buildEnvPresetOptions();
  document.getElementById('modal-container').innerHTML =
    '<div class="modal-overlay" onclick="onModalOverlayClick(event)">' +
    '<div class="modal">' +
    '<div class="modal-header"><h2>Edit: ' + esc(name) + '</h2>' +
    '<button class="modal-close" onclick="closeModal()">\\u2715</button></div>' +
    '<div class="modal-body">' +
    '<div class="form-group"><label class="form-label">Name</label>' +
    '<input type="text" value="' + esc(secret.name) + '" disabled style="opacity:0.5"></div>' +
    '<div class="form-group"><label class="form-label">Env Var</label>' +
    '<input type="text" id="edit-env" value="' + esc(secret.env_name) + '"></div>' +
    '<div class="form-group"><label class="form-label">New Value <span style="font-weight:400;text-transform:none;letter-spacing:0">(leave blank to keep current)</span></label>' +
    '<div class="password-wrapper"><input type="password" id="edit-value" placeholder="Enter new value to rotate...">' +
    '<button class="password-toggle" onclick="togglePw(this)" type="button" title="Show/hide">\\u25cf</button></div></div>' +
    '<div class="form-group"><label class="form-label">Description</label>' +
    '<input type="text" id="edit-desc" value="' + esc(secret.description || '') + '" placeholder="Optional description"></div>' +
    '<div class="form-group"><label class="form-label">Targets</label>' +
    '<div class="target-rows" id="edit-targets"></div>' +
    '<button class="btn btn-ghost btn-sm" onclick="addTargetRow(\\'edit-targets\\',\\'\\',\\'\\')" style="margin-top:8px">+ Add Target</button></div>' +
    '</div>' +
    '<div class="modal-footer">' +
    '<button class="btn btn-sm" onclick="closeModal()">Cancel</button>' +
    '<button class="btn btn-primary btn-sm" onclick="doEdit(\\''+esc(name)+'\\')">Save Changes</button>' +
    '</div></div></div>';
  // populate existing targets
  if (secret.targets) {
    for (var i = 0; i < secret.targets.length; i++) {
      var t = secret.targets[i];
      addTargetRow('edit-targets', t.platform || '', t.project || t.file || '');
    }
  }
}

async function doEdit(name) {
  var env = document.getElementById('edit-env').value.trim();
  var value = document.getElementById('edit-value').value;
  var desc = document.getElementById('edit-desc').value.trim();
  var targets = collectTargets('edit-targets');
  if (!env) { toast('Env Var is required', 'fail'); return; }
  var body = { name: name, env: env, description: desc, targets: targets };
  if (value) body.value = value;
  var r = await api('POST', '/api/edit', body);
  closeModal();
  if (r.ok) toast('Updated: ' + name);
  else toast('Failed: ' + (r.error || 'unknown'), 'fail');
  refresh();
}

/* ===================== EXPORT ===================== */
async function updateExportPreview() {
  var fmt = document.getElementById('export-format').value;
  var env = document.getElementById('export-env') ? document.getElementById('export-env').value : '';
  var r = await api('GET', '/api/export?format=' + encodeURIComponent(fmt) + '&env=' + encodeURIComponent(env));
  var preview = document.getElementById('export-preview');
  if (r.content !== undefined) {
    preview.innerHTML = '<div class="copy-overlay" id="copy-overlay">Copied!</div>' + esc(r.content);
  } else {
    preview.innerHTML = '<div class="copy-overlay" id="copy-overlay">Copied!</div><span style="color:var(--muted)">No secrets to export</span>';
  }
}

async function copyExport() {
  var preview = document.getElementById('export-preview');
  var text = preview.textContent.replace('Copied!', '').trim();
  try {
    await navigator.clipboard.writeText(text);
    var overlay = document.getElementById('copy-overlay');
    overlay.classList.add('show');
    setTimeout(function() { overlay.classList.remove('show'); }, 1200);
    toast('Copied to clipboard', 'info');
  } catch (e) {
    toast('Failed to copy', 'fail');
  }
}

/* ===================== AUDIT PANEL ===================== */
function renderAuditPanel(issues) {
  var panel = document.getElementById('audit-panel');
  if (!issues || issues.length === 0) {
    panel.className = 'audit-panel open';
    panel.innerHTML =
      '<div class="audit-panel-header"><span>\\u2713 Audit Results</span>' +
      '<button class="btn btn-ghost btn-sm" onclick="document.getElementById(\\'audit-panel\\').className=\\'audit-panel\\'">\\u2715</button></div>' +
      '<div class="audit-panel-body" style="color:var(--green);font-size:14px;font-weight:500">All secrets are in sync. No issues found.</div>';
    return;
  }
  var html = '<div class="audit-panel-header"><span>\\u26a0 ' + issues.length + ' Issue(s) Found</span>' +
    '<button class="btn btn-ghost btn-sm" onclick="document.getElementById(\\'audit-panel\\').className=\\'audit-panel\\'">\\u2715</button></div>' +
    '<div class="audit-panel-body">';
  for (var i = 0; i < issues.length; i++) {
    html += '<div class="audit-issue"><span class="status-dot status-dot-fail"></span><span class="mono">' + esc(issues[i]) + '</span></div>';
  }
  html += '</div>';
  panel.className = 'audit-panel open';
  panel.innerHTML = html;
}

/* ===================== RENDERING ===================== */
function render() {
  renderStats();
  renderStatusTable();
  renderSecrets();
  renderHistory();
  renderConfig();
  renderExportEnvOptions();
  updateExportPreview();
  // tab counts
  document.getElementById('tc-status').textContent = data.status.length;
  document.getElementById('tc-secrets').textContent = data.config.secrets.length;
}

function renderStats() {
  var total = data.status.length;
  var synced = data.status.filter(function(s) {
    return s.keychain && Object.values(s.targets).every(function(v) { return v !== false; });
  }).length;
  var drifted = total - synced;
  var now = new Date();
  var time = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  var html =
    '<div class="stat-item"><div class="stat-value">' + total + '</div><div class="stat-label">Total Secrets</div></div>' +
    '<div class="stat-divider"></div>' +
    '<div class="stat-item"><div class="stat-value" style="color:var(--green)">' + synced + '</div><div class="stat-label">In Sync</div></div>';
  if (drifted > 0) {
    html += '<div class="stat-divider"></div>' +
      '<div class="stat-item"><div class="stat-value" style="color:var(--red)">' + drifted + '</div><div class="stat-label">Drifted</div></div>';
  }
  html += '<div class="stat-divider"></div>' +
    '<div class="stat-item"><div class="stat-value" style="font-size:18px;padding-top:4px">' + time + '</div><div class="stat-label">Last Check</div></div>';
  document.getElementById('stats-bar').innerHTML = html;
}

function renderStatusTable() {
  if (data.status.length === 0) {
    document.getElementById('status-body').innerHTML =
      '<tr><td colspan="4"><div class="empty-state"><div class="empty-icon">&#x1f512;</div>' +
      '<h3>No secrets configured</h3><p>Add your first secret in the Secrets tab.</p></div></td></tr>';
    return;
  }
  var html = '';
  for (var i = 0; i < data.status.length; i++) {
    var s = data.status[i];
    var kcClass = s.keychain ? 'status-dot-ok' : 'status-dot-fail';
    var kcText = s.keychain ? 'Stored' : 'Missing';
    var targets = '';
    var tKeys = Object.keys(s.targets);
    if (tKeys.length === 0) {
      targets = '<span style="color:var(--subtle)">No targets</span>';
    } else {
      targets = '<div class="badges-wrap">';
      for (var j = 0; j < tKeys.length; j++) {
        var k = tKeys[j];
        var v = s.targets[k];
        var cls = v === true ? 'badge-ok' : v === false ? 'badge-fail' : 'badge-warn';
        var sym = v === true ? '\\u2713' : v === false ? '\\u2717' : '\\u2014';
        targets += '<span class="badge ' + cls + '">' + sym + ' ' + esc(k) + '</span>';
      }
      targets += '</div>';
    }
    html += '<tr>' +
      '<td><span class="mono" style="font-weight:500">' + esc(s.env_name) + '</span><br><span style="font-size:11px;color:var(--subtle)">' + esc(s.name) + '</span></td>' +
      '<td><span class="status-dot ' + kcClass + '"></span>' + kcText + '</td>' +
      '<td>' + targets + '</td>' +
      '<td><div class="btn-group">' +
      '<button class="btn btn-sm" onclick="syncOne(\\''+esc(s.name)+'\\')">Sync</button>' +
      '<button class="btn btn-sm" onclick="showRotateModal(\\''+esc(s.name)+'\\')">Rotate</button>' +
      '<button class="btn btn-sm" onclick="showEditModal(\\''+esc(s.name)+'\\')">Edit</button>' +
      '</div></td></tr>';
  }
  document.getElementById('status-body').innerHTML = html;
}

function renderSecrets() {
  var filter = (document.getElementById('secrets-search') || {}).value || '';
  filter = filter.toLowerCase();
  var secrets = data.config.secrets;
  if (filter) {
    secrets = secrets.filter(function(s) {
      return s.name.toLowerCase().indexOf(filter) >= 0 ||
             s.env_name.toLowerCase().indexOf(filter) >= 0 ||
             (s.description || '').toLowerCase().indexOf(filter) >= 0;
    });
  }
  if (secrets.length === 0 && data.config.secrets.length === 0) {
    document.getElementById('secrets-body').innerHTML =
      '<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">&#x2795;</div>' +
      '<h3>No secrets yet</h3><p>Click "+ Add Secret" to get started.</p></div></td></tr>';
    return;
  }
  if (secrets.length === 0) {
    document.getElementById('secrets-body').innerHTML =
      '<tr><td colspan="5"><div class="empty-state"><p>No secrets match your filter.</p></div></td></tr>';
    return;
  }
  var html = '';
  for (var i = 0; i < secrets.length; i++) {
    var s = secrets[i];
    var labels = s.target_labels || [];
    var badgesHtml = '';
    if (labels.length > 0) {
      badgesHtml = '<div class="badges-wrap">';
      for (var j = 0; j < labels.length; j++) {
        badgesHtml += '<span class="badge badge-platform">' + esc(labels[j]) + '</span>';
      }
      badgesHtml += '</div>';
    } else {
      badgesHtml = '<span style="color:var(--subtle)">&mdash;</span>';
    }
    html += '<tr>' +
      '<td><span class="mono" style="font-weight:500">' + esc(s.name) + '</span></td>' +
      '<td class="mono">' + esc(s.env_name) + '</td>' +
      '<td>' + (s.description ? esc(s.description) : '<span style="color:var(--subtle)">&mdash;</span>') + '</td>' +
      '<td>' + badgesHtml + '</td>' +
      '<td><div class="btn-group">' +
      '<button class="btn btn-sm" onclick="showEditModal(\\''+esc(s.name)+'\\')">Edit</button>' +
      '<button class="btn btn-sm btn-danger" onclick="deleteSecret(\\''+esc(s.name)+'\\')">Delete</button>' +
      '</div></td></tr>';
  }
  document.getElementById('secrets-body').innerHTML = html;
}

function renderHistory() {
  var html = '';
  var names = Object.keys(data.history);
  if (names.length === 0 || names.every(function(n) { return data.history[n].length === 0; })) {
    html = '<div class="empty-state"><div class="empty-icon">&#x1f4dc;</div>' +
      '<h3>No history recorded</h3><p>Version history appears here after secrets are added or rotated.</p></div>';
    document.getElementById('history-body').innerHTML = html;
    return;
  }
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    var versions = data.history[name];
    if (versions.length === 0) continue;
    html += '<div class="card history-card" onclick="this.classList.toggle(\\'open\\')">' +
      '<h3><span class="mono">' + esc(name) + '</span>' +
      '<span class="badge badge-env" style="font-size:10px">' + versions.length + ' version' + (versions.length !== 1 ? 's' : '') + '</span>' +
      '<span class="expand-icon">&#x25bc;</span></h3>' +
      '<div class="history-versions">';
    var sorted = versions.slice().reverse();
    for (var j = 0; j < sorted.length; j++) {
      var v = sorted[j];
      html += '<div class="history-version">' +
        '<span class="ver-num">v' + v.version + '</span>' +
        '<span>' + esc(v.timestamp) + '</span>' +
        '<span class="ver-fp mono">' + esc(v.fingerprint) + '</span>' +
        '</div>';
    }
    html += '</div></div>';
  }
  document.getElementById('history-body').innerHTML = html;
}

function renderConfig() {
  var cfg = data.config;
  var html = '';

  // Keychain Service
  html += '<div class="config-section"><h2>&#x1f511; Keychain</h2>' +
    '<div class="card"><dl class="config-kv" style="display:flex"><dt>Service Prefix</dt><dd class="mono">' +
    esc(cfg.keychain_service || 'banto-sync') + '</dd></dl></div></div>';

  // Environments
  if (cfg.environments && cfg.environments.length > 0) {
    html += '<div class="config-section"><h2>&#x1f30d; Environments</h2><div class="card">' +
      '<div class="badges-wrap" style="margin-bottom:8px">';
    for (var i = 0; i < cfg.environments.length; i++) {
      html += '<span class="badge badge-env">' + esc(cfg.environments[i]) + '</span>';
    }
    html += '</div>';
    if (cfg.default_environment) {
      html += '<p style="font-size:12px;color:var(--muted)">Default: <span class="mono">' + esc(cfg.default_environment) + '</span></p>';
    }
    html += '</div></div>';
  }

  // Notifiers
  if (cfg.notifiers && cfg.notifiers.length > 0) {
    html += '<div class="config-section"><h2>&#x1f514; Notifiers</h2>';
    for (var i = 0; i < cfg.notifiers.length; i++) {
      var n = cfg.notifiers[i];
      html += '<div class="card"><h3>' + esc(n.name) + '</h3>' +
        '<div class="badges-wrap">';
      for (var j = 0; j < n.events.length; j++) {
        html += '<span class="badge badge-warn">' + esc(n.events[j]) + '</span>';
      }
      html += '</div></div>';
    }
    html += '</div>';
  }

  // Platforms in use
  var usedPlatforms = {};
  for (var i = 0; i < cfg.secrets.length; i++) {
    var labels = cfg.secrets[i].target_labels || [];
    for (var j = 0; j < labels.length; j++) {
      usedPlatforms[labels[j]] = true;
    }
  }
  var platformList = Object.keys(usedPlatforms).sort();
  html += '<div class="config-section"><h2>&#x2601; Platforms in Use</h2><div class="card">';
  if (platformList.length > 0) {
    html += '<div class="badges-wrap">';
    for (var i = 0; i < platformList.length; i++) {
      html += '<span class="badge badge-platform">' + esc(platformList[i]) + '</span>';
    }
    html += '</div>';
  } else {
    html += '<p>No platforms configured yet.</p>';
  }
  html += '</div></div>';

  // Available drivers
  var drivers = data.drivers && data.drivers.length ? data.drivers : PLATFORMS;
  html += '<div class="config-section"><h2>&#x1f50c; Available Drivers (' + drivers.length + ')</h2><div class="card">' +
    '<div class="badges-wrap">';
  for (var i = 0; i < drivers.length; i++) {
    var used = platformList.indexOf(drivers[i]) >= 0 || usedPlatforms[drivers[i]];
    html += '<span class="badge ' + (used ? 'badge-ok' : 'badge-env') + '" style="font-size:10px">' + esc(drivers[i]) + '</span>';
  }
  html += '</div></div></div>';

  document.getElementById('config-body').innerHTML = html;
}

function renderExportEnvOptions() {
  var sel = document.getElementById('export-env');
  var group = document.getElementById('export-env-group');
  if (!sel || !group) return;
  var envs = data.config.environments || [];
  if (envs.length === 0) { group.style.display = 'none'; return; }
  group.style.display = 'block';
  var html = '<option value="">(default)</option>';
  for (var i = 0; i < envs.length; i++) {
    html += '<option value="' + esc(envs[i]) + '">' + esc(envs[i]) + '</option>';
  }
  sel.innerHTML = html;
}

/* ===================== TAB SWITCHING ===================== */
document.getElementById('tab-bar').addEventListener('click', function(e) {
  var tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
  tab.classList.add('active');
  document.getElementById(tab.dataset.panel).classList.add('active');
});

/* ===================== INIT ===================== */
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
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            # Inject the platform list into the HTML
            platform_json = json.dumps(ALL_PLATFORMS)
            html = _HTML.replace("PLATFORM_LIST_PLACEHOLDER", platform_json)
            self.wfile.write(html.encode("utf-8"))
        elif path == "/api/status":
            self._json_response(_build_status_json(self.config))
        elif path == "/api/history":
            self._json_response(_build_history_json(self.config))
        elif path == "/api/config":
            self._json_response(_build_config_json(self.config))
        elif path == "/api/drivers":
            self._json_response({"drivers": ALL_PLATFORMS})
        elif path == "/api/export":
            self._handle_export(params)
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/sync":
            self._handle_sync(body)
        elif self.path == "/api/add":
            self._handle_add(body)
        elif self.path == "/api/edit":
            self._handle_edit(body)
        elif self.path == "/api/delete":
            self._handle_delete(body)
        elif self.path == "/api/rotate":
            self._handle_rotate(body)
        elif self.path == "/api/audit":
            self._handle_audit()
        elif self.path == "/api/validate":
            self._handle_validate()
        elif self.path == "/api/validate-keychain":
            self._handle_validate_keychain()
        else:
            self.send_error(404)

    # ── Sync ──────────────────────────────────────────────
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

    # ── Add ───────────────────────────────────────────────
    def _handle_add(self, body: dict) -> None:
        name = body.get("name", "").strip()
        env = body.get("env", "").strip()
        value = body.get("value", "")
        desc = body.get("description", "")
        raw_targets = body.get("targets", [])

        # Legacy single-target support
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

        targets: list[Target] = _parse_targets(raw_targets, target_str)

        entry = SecretEntry(name=name, account=name, env_name=env,
                            description=desc, targets=targets)
        self.config.add_secret(entry)
        self.config.save(self.config_path)

        history = HistoryStore()
        history.record(name, value, self.config.keychain_service)

        self._json_response({"ok": True})

    # ── Edit ──────────────────────────────────────────────
    def _handle_edit(self, body: dict) -> None:
        name = body.get("name", "").strip()
        if not name:
            self._json_response({"ok": False, "error": "name is required"})
            return

        entry = self.config.get_secret(name)
        if not entry:
            self._json_response({"ok": False, "error": f"'{name}' not found"})
            return

        # Update env_name if provided
        new_env = body.get("env", "").strip()
        if new_env:
            entry.env_name = new_env

        # Update description
        if "description" in body:
            entry.description = body["description"]

        # Update targets if provided
        if "targets" in body:
            entry.targets = _parse_targets(body["targets"], "")

        # Rotate value if provided
        new_value = body.get("value", "")
        if new_value:
            kc = KeychainStore(service_prefix=self.config.keychain_service)
            if not kc.store(entry.account, new_value):
                self._json_response({"ok": False, "error": "Keychain update failed"})
                return
            history = HistoryStore()
            history.record(name, new_value, self.config.keychain_service)

        self.config.save(self.config_path)
        self._json_response({"ok": True})

    # ── Delete ────────────────────────────────────────────
    def _handle_delete(self, body: dict) -> None:
        name = body.get("name", "")
        if not name:
            self._json_response({"ok": False, "error": "name required"})
            return
        report = remove_secret(self.config, name)
        self.config.save(self.config_path)
        self._json_response({"ok": True, "results": report.ok_count})

    # ── Rotate ────────────────────────────────────────────
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

    # ── Audit ─────────────────────────────────────────────
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

    # ── Validate ──────────────────────────────────────────
    def _handle_validate(self) -> None:
        from .validate import validate_key

        kc = KeychainStore(service_prefix=self.config.keychain_service)
        results: list[dict] = []
        for name, entry in self.config.secrets.items():
            value = kc.get(entry.account)
            if not value:
                results.append({
                    "name": name,
                    "provider": name,
                    "valid": False,
                    "message": "Not found in Keychain",
                })
                continue
            vr = validate_key(name, value)
            results.append({
                "name": name,
                "provider": vr.provider,
                "valid": vr.valid,
                "message": vr.message,
            })
        self._json_response({"ok": True, "results": results})

    def _handle_validate_keychain(self) -> None:
        """Scan Keychain for known provider keys and validate them."""
        import re
        import subprocess

        from .validate import validate_key, SERVICE_PATTERNS, should_exclude

        result = subprocess.run(
            ["security", "dump-keychain"], capture_output=True, text=True,
        )
        if result.returncode != 0:
            self._json_response({"ok": False, "results": [], "error": "Keychain dump failed"})
            return

        svce_re = re.compile(r'"svce"<blob>="([^"]*)"')
        acct_re = re.compile(r'"acct"<blob>="([^"]*)"')

        entries: list[tuple[str, str]] = []
        current_attrs: dict[str, str] = {}
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("class:"):
                if "svce" in current_attrs:
                    entries.append((current_attrs.get("svce", ""), current_attrs.get("acct", "")))
                current_attrs = {}
                continue
            m = svce_re.search(stripped)
            if m:
                current_attrs["svce"] = m.group(1)
            m = acct_re.search(stripped)
            if m:
                current_attrs["acct"] = m.group(1)
        if "svce" in current_attrs:
            entries.append((current_attrs.get("svce", ""), current_attrs.get("acct", "")))

        results: list[dict] = []
        seen: set[str] = set()
        for svc, acct in entries:
            if not svc or svc in seen or should_exclude(svc):
                continue
            svc_lower = svc.lower()
            for pattern in SERVICE_PATTERNS:
                if pattern in svc_lower:
                    seen.add(svc)
                    val = subprocess.run(
                        ["security", "find-generic-password", "-s", svc, "-w"],
                        capture_output=True, text=True,
                    ).stdout.strip()
                    if val:
                        vr = validate_key(svc, val)
                        results.append({
                            "name": svc,
                            "provider": vr.provider,
                            "valid": vr.valid,
                            "message": vr.message,
                        })
                    break

        self._json_response({"ok": True, "results": results})

    # ── Export ────────────────────────────────────────────
    def _handle_export(self, params: dict) -> None:
        fmt = (params.get("format", ["env"])[0]).strip()
        env_name = (params.get("env", [""])[0]).strip()

        kc = KeychainStore(service_prefix=self.config.keychain_service)

        if env_name:
            resolved = self.config.resolve_environment(env_name)
        else:
            resolved = dict(self.config.secrets)

        if not resolved:
            self._json_response({"content": "", "format": fmt})
            return

        secrets: dict[str, str] = {}
        for _name, entry in resolved.items():
            val = kc.get(entry.account)
            secrets[entry.env_name] = val or ""

        content = ""
        if fmt == "env":
            lines = []
            for k, v in secrets.items():
                if "\n" in v or "#" in v or " " in v:
                    v = '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
                lines.append(f"{k}={v}")
            content = "\n".join(lines)
        elif fmt == "json":
            content = json.dumps(secrets, indent=2, ensure_ascii=False)
        elif fmt == "docker":
            lines = []
            for k, v in secrets.items():
                lines.append(f"{k}={v}")
            content = "\n".join(lines)
        else:
            self._json_response({"error": f"Unknown format: {fmt}"})
            return

        # SECURITY: Mask values in the preview — show only first 4 chars
        masked: dict[str, str] = {}
        for k, v in secrets.items():
            if v:
                visible = min(4, len(v))
                masked[k] = v[:visible] + "*" * max(0, len(v) - visible)
            else:
                masked[k] = "(empty)"

        masked_content = ""
        if fmt == "env" or fmt == "docker":
            lines = []
            for k, v in masked.items():
                lines.append(f"{k}={v}")
            masked_content = "\n".join(lines)
        elif fmt == "json":
            masked_content = json.dumps(masked, indent=2, ensure_ascii=False)

        self._json_response({
            "content": masked_content,
            "format": fmt,
            "count": len(secrets),
        })

    # ── Response helper ───────────────────────────────────
    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_targets(raw_targets: list, target_str: str = "") -> list[Target]:
    """Parse targets from an array of dicts or a legacy colon-separated string."""
    targets: list[Target] = []
    if isinstance(raw_targets, list):
        for t in raw_targets:
            if isinstance(t, dict) and t.get("platform"):
                targets.append(Target(
                    platform=t["platform"],
                    project=t.get("project", ""),
                    file=t.get("file", ""),
                ))
    # Legacy single-target fallback
    if not targets and target_str and ":" in target_str:
        platform, project = target_str.split(":", 1)
        if platform == "local":
            targets.append(Target(platform="local", file=project))
        else:
            targets.append(Target(platform=platform, project=project))
    return targets


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

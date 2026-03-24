# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""ChatGPT App connection helper.

banto runs on the user's Mac (Keychain access required).
This module handles:
  1. Starting banto-mcp in HTTP mode
  2. Setting up a tunnel (ngrok or cloudflared)
  3. Providing the HTTPS URL for ChatGPT Connector registration

Usage:
    banto chatgpt connect              # auto-detect tunnel tool
    banto chatgpt connect --ngrok      # force ngrok
    banto chatgpt connect --cloudflared  # force cloudflared
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time


def _find_tunnel_tool() -> str | None:
    """Detect available tunnel tool."""
    for tool in ["ngrok", "cloudflared"]:
        try:
            subprocess.run([tool, "version"], capture_output=True, timeout=5)
            return tool
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _start_mcp_server(port: int, path_token: str = "") -> subprocess.Popen:
    """Start banto-mcp in HTTP mode with optional capability URL path."""
    env = os.environ.copy()
    if path_token:
        env["BANTO_MCP_PATH_TOKEN"] = path_token
    return subprocess.Popen(
        [sys.executable, "-m", "banto.mcp_server",
         "--transport", "http", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _start_ngrok(port: int) -> tuple[subprocess.Popen, str]:
    """Start ngrok tunnel and return (process, public_url)."""
    proc = subprocess.Popen(
        ["ngrok", "http", str(port), "--log", "stdout", "--log-format", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for tunnel URL
    url = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        try:
            data = json.loads(line)
            if data.get("url") and data["url"].startswith("https://"):
                url = data["url"]
                break
        except (json.JSONDecodeError, KeyError):
            pass
    if not url:
        # Fallback: query ngrok API
        try:
            import urllib.request
            resp = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5)
            tunnels = json.loads(resp.read())
            for t in tunnels.get("tunnels", []):
                if t.get("public_url", "").startswith("https://"):
                    url = t["public_url"]
                    break
        except Exception:
            pass
    return proc, url


def _start_cloudflared(port: int) -> tuple[subprocess.Popen, str]:
    """Start cloudflared tunnel and return (process, public_url)."""
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # cloudflared outputs URL to stderr
    url = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.2)
            continue
        text = line.decode("utf-8", errors="replace")
        if ".trycloudflare.com" in text:
            import re
            m = re.search(r"(https://[^\s]+\.trycloudflare\.com)", text)
            if m:
                url = m.group(1)
                break
    return proc, url


def connect(args: list[str]) -> None:
    """Start banto MCP server + tunnel for ChatGPT connection."""
    port = 8385
    tool = None

    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a == "--ngrok":
            tool = "ngrok"
        elif a == "--cloudflared":
            tool = "cloudflared"

    if tool is None:
        tool = _find_tunnel_tool()
        if tool is None:
            print("Error: No tunnel tool found.", file=sys.stderr)
            print("Install one of:", file=sys.stderr)
            print("  brew install ngrok", file=sys.stderr)
            print("  brew install cloudflared", file=sys.stderr)
            sys.exit(1)

    # Generate capability URL path token for secure tunneled access
    path_token = secrets.token_urlsafe(16)
    mcp_path = f"/mcp-{path_token}"

    print(f"Starting banto MCP server on port {port}...")
    mcp_proc = _start_mcp_server(port, path_token=path_token)
    time.sleep(1)

    if mcp_proc.poll() is not None:
        print("Error: MCP server failed to start.", file=sys.stderr)
        sys.exit(1)

    print(f"Starting {tool} tunnel...")
    if tool == "ngrok":
        tunnel_proc, url = _start_ngrok(port)
    else:
        tunnel_proc, url = _start_cloudflared(port)

    if not url:
        print(f"Error: Could not get tunnel URL from {tool}.", file=sys.stderr)
        mcp_proc.terminate()
        tunnel_proc.terminate()
        sys.exit(1)

    mcp_url = f"{url}{mcp_path}"

    print()
    print("=" * 60)
    print("  banto is ready for ChatGPT")
    print("=" * 60)
    print()
    print(f"  MCP Endpoint: {mcp_url}")
    print()
    print("  The URL contains a secret token — treat it like a password.")
    print("  Anyone with this URL can access your banto instance.")
    print()
    print("  To connect in ChatGPT:")
    print("  1. Settings -> Connectors -> Create")
    print(f"  2. Connector URL: {mcp_url}")
    print("  3. Name: banto")
    print("  4. Description: Local-first secret management")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    try:
        mcp_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        mcp_proc.terminate()
        tunnel_proc.terminate()
        print("Stopped.")

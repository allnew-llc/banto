# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Auto-detect and configure sync targets.

banto sync setup vercel:my-project
  1. Queries the platform for existing env var names
  2. Scans Keychain for matching entries
  3. Registers matches in sync.json
  4. Reports matches and gaps

Supports: vercel, cloudflare-pages (extensible).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, SecretEntry, SyncConfig, Target
from .validate import EXCLUDED_SERVICES, EXCLUDED_PATTERNS


# ── Env var → Keychain service name mapping ──────────────────────
# Maps common env var names to known Keychain service patterns.
# Order matters: first match wins.

ENV_TO_KEYCHAIN: list[tuple[str, list[str]]] = [
    # OpenAI
    ("OPENAI_API_KEY", ["claude-mcp-openai", "banto-openai", "openai"]),
    # Anthropic
    ("ANTHROPIC_API_KEY", ["claude-mcp-anthropic", "banto-anthropic", "anthropic"]),
    # Gemini / Google
    ("GEMINI_API_KEY", ["claude-mcp-gemini", "banto-gemini", "gemini"]),
    ("GOOGLE_API_KEY", ["claude-mcp-gemini", "banto-gemini"]),
    # GitHub
    ("GITHUB_TOKEN", ["claude-mcp-github", "banto-github"]),
    ("GITHUB_ACCESS_TOKEN", ["claude-mcp-github", "banto-github"]),
    # Cloudflare
    ("CLOUDFLARE_API_TOKEN", ["cloudflare-api-token", "banto-cloudflare"]),
    ("CF_API_TOKEN", ["cloudflare-api-token"]),
    # xAI
    ("XAI_API_KEY", ["claude-mcp-xai", "banto-xai", "xai_api_key"]),
    # LINE
    ("LINE_CHANNEL_ACCESS_TOKEN", ["line-clawboy-channel-token"]),
    ("LINE_CHANNEL_SECRET", ["line-clawboy-channel-secret"]),
    ("LINE_OWNER_USER_ID", ["line-clawboy-owner-user-id"]),
    # Azure
    ("AZURE_CLIENT_ID", ["claude-mcp-azure-client-id"]),
    ("AZURE_CLIENT_SECRET", ["claude-mcp-azure-client-secret"]),
    ("AZURE_TENANT_ID", ["claude-mcp-azure-tenant-id"]),
    # AWS
    ("AWS_ACCESS_KEY_ID", ["banto-aws-access"]),
    ("AWS_SECRET_ACCESS_KEY", ["banto-aws-secret"]),
    # Generic patterns (fallback: try banto-{name} and env var lowercased)
]


def _keychain_exists(service: str) -> bool:
    """Check if a Keychain entry exists by raw service name."""
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service],
        capture_output=True,
    )
    return r.returncode == 0


def _is_excluded(service: str) -> bool:
    """Check if a Keychain service should be excluded (OAuth tokens, etc.)."""
    if service in EXCLUDED_SERVICES:
        return True
    lower = service.lower()
    return any(pat in lower for pat in EXCLUDED_PATTERNS)


def _find_keychain_match(env_var: str) -> str | None:
    """Find a Keychain service name that matches an env var name."""
    # Check known mappings
    for known_env, candidates in ENV_TO_KEYCHAIN:
        if env_var == known_env:
            for svc in candidates:
                if not _is_excluded(svc) and _keychain_exists(svc):
                    return svc
            return None

    # Fallback heuristics
    name = env_var.lower().replace("_", "-")
    for prefix in ["claude-mcp-", "banto-", ""]:
        candidate = f"{prefix}{name}" if prefix else name
        if not _is_excluded(candidate) and _keychain_exists(candidate):
            return candidate

    return None


# ── Platform env var discovery ───────────────────────────────────

def _list_vercel_env_vars(project: str) -> list[str]:
    """List env var names from a Vercel project.

    Always passes --project to scope the query to the requested project.
    """
    try:
        r = subprocess.run(
            ["vercel", "env", "ls", "production", "--project", project, "--yes"],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path.home()),  # avoid link requirement
        )
        # Parse output: env var names are listed one per line
        env_vars = []
        for line in r.stdout.splitlines():
            line = line.strip()
            # Skip headers and empty lines
            if not line or line.startswith("│") or line.startswith("┌") or line.startswith("└"):
                continue
            if line.startswith("├") or line.startswith("─"):
                continue
            # Extract env var name (first word, all caps with underscores)
            m = re.match(r"^([A-Z][A-Z0-9_]+)", line)
            if m:
                env_vars.append(m.group(1))
        return env_vars
    except (subprocess.SubprocessError, OSError):
        return []


def _list_cloudflare_secrets(project: str) -> list[str]:
    """List secret names from a Cloudflare Pages project."""
    try:
        r = subprocess.run(
            ["wrangler", "pages", "secret", "list", "--project-name", project],
            capture_output=True, text=True, timeout=15,
        )
        env_vars = []
        for line in r.stdout.splitlines():
            m = re.match(r"\s*-\s+([A-Z][A-Z0-9_]+):", line)
            if m:
                env_vars.append(m.group(1))
        return env_vars
    except (subprocess.SubprocessError, OSError):
        return []


PLATFORM_SCANNERS = {
    "vercel": _list_vercel_env_vars,
    "cloudflare-pages": _list_cloudflare_secrets,
}


# ── Setup result ─────────────────────────────────────────────────

@dataclass
class SetupMatch:
    env_var: str
    keychain_service: str | None  # None = no match found
    status: str  # "matched", "missing", "already_configured", "discovery_empty"


def run_setup(
    platform: str,
    project: str,
    config: SyncConfig | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    guess: bool = False,
) -> list[SetupMatch]:
    """Auto-detect env vars on a platform and match to Keychain entries.

    Returns list of matches. If not dry_run, also registers in sync.json.

    When discovery returns no results:
      - Without guess=True: returns a single "discovery_empty" entry (fail-closed).
      - With guess=True: falls back to known env var catalog (best-effort).
    """
    cfg = config or SyncConfig.load(config_path or DEFAULT_CONFIG_PATH)
    cfg_path = config_path or DEFAULT_CONFIG_PATH

    # Discover env vars on the platform
    scanner = PLATFORM_SCANNERS.get(platform)
    if scanner is None:
        env_vars: list[str] = []
    else:
        env_vars = scanner(project)

    if not env_vars:
        if guess:
            # Explicit fallback: use known env var catalog as best-effort candidates
            env_vars = [e for e, _ in ENV_TO_KEYCHAIN]
        else:
            # Fail closed: do not silently guess
            return [SetupMatch(
                env_var="(none)",
                keychain_service=None,
                status="discovery_empty",
            )]

    matches: list[SetupMatch] = []
    registered = 0

    for env_var in env_vars:
        # Skip if already in sync.json
        existing = None
        for name, entry in cfg.secrets.items():
            if entry.env_name == env_var:
                existing = name
                break
        if existing:
            matches.append(SetupMatch(env_var, None, "already_configured"))
            continue

        # Find Keychain match
        kc_service = _find_keychain_match(env_var)
        if kc_service is None:
            matches.append(SetupMatch(env_var, None, "missing"))
            continue

        matches.append(SetupMatch(env_var, kc_service, "matched"))

        if not dry_run:
            # Register in sync.json
            name = env_var.lower().replace("_", "-")
            target = Target(
                platform=platform,
                project=project,
            ) if platform != "local" else Target(platform="local", file=project)

            entry = SecretEntry(
                name=name,
                account=kc_service,
                env_name=env_var,
                targets=[target],
            )
            cfg.add_secret(entry)
            registered += 1

    if not dry_run and registered > 0:
        cfg.save(cfg_path)

    return matches

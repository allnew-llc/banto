# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""API key validation — lightweight health checks per provider.

Each validator makes a minimal, read-only API call to verify the key
is valid. No data is created, modified, or deleted. No values are logged.

Usage:
    from banto.sync.validate import validate_key

    result = validate_key("openai", "sk-...")
    # result.valid, result.provider, result.message
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of a key validation check."""

    provider: str
    valid: bool
    message: str = ""
    status_code: int = 0


def _http_get(url: str, headers: dict[str, str], timeout: int = 10) -> tuple[int, str]:
    """Minimal HTTP GET using stdlib. Returns (status_code, body)."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except (urllib.error.URLError, OSError) as e:
        return 0, str(type(e).__name__)


def _validate_openai(key: str) -> ValidationResult:
    """OpenAI: GET /v1/models (list models, read-only)."""
    status, body = _http_get(
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        return ValidationResult("openai", True, "Key valid")
    if status == 401:
        return ValidationResult("openai", False, "Invalid API key", status)
    if status == 429:
        return ValidationResult("openai", True, "Rate limited but key accepted", status)
    return ValidationResult("openai", False, f"HTTP {status}", status)


def _validate_anthropic(key: str) -> ValidationResult:
    """Anthropic: GET /v1/models (list models, read-only)."""
    status, body = _http_get(
        "https://api.anthropic.com/v1/models",
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    if status == 200:
        return ValidationResult("anthropic", True, "Key valid")
    if status == 401:
        return ValidationResult("anthropic", False, "Invalid API key", status)
    if status == 429:
        return ValidationResult("anthropic", True, "Rate limited but key accepted", status)
    return ValidationResult("anthropic", False, f"HTTP {status}", status)


def _validate_gemini(key: str) -> ValidationResult:
    """Google Gemini: GET /v1beta/models (list models, read-only)."""
    status, body = _http_get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        {},
    )
    if status == 200:
        return ValidationResult("gemini", True, "Key valid")
    if status == 400 or status == 403:
        return ValidationResult("gemini", False, "Invalid API key", status)
    return ValidationResult("gemini", False, f"HTTP {status}", status)


def _validate_github(key: str) -> ValidationResult:
    """GitHub: GET /user (read-only, returns authenticated user)."""
    status, body = _http_get(
        "https://api.github.com/user",
        {
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "banto-sync",
        },
    )
    if status == 200:
        return ValidationResult("github", True, "Token valid")
    if status == 401:
        return ValidationResult("github", False, "Invalid token", status)
    return ValidationResult("github", False, f"HTTP {status}", status)


def _validate_cloudflare(key: str) -> ValidationResult:
    """Cloudflare: GET /client/v4/user/tokens/verify (read-only)."""
    status, body = _http_get(
        "https://api.cloudflare.com/client/v4/user/tokens/verify",
        {"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        try:
            data = json.loads(body)
            if data.get("success"):
                return ValidationResult("cloudflare", True, "Token valid")
        except json.JSONDecodeError:
            pass
        return ValidationResult("cloudflare", True, "HTTP 200")
    if status == 401:
        return ValidationResult("cloudflare", False, "Invalid token", status)
    return ValidationResult("cloudflare", False, f"HTTP {status}", status)


def _validate_xai(key: str) -> ValidationResult:
    """xAI/Grok: GET /v1/models (OpenAI-compatible endpoint)."""
    status, body = _http_get(
        "https://api.x.ai/v1/models",
        {"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        return ValidationResult("xai", True, "Key valid")
    if status == 401:
        return ValidationResult("xai", False, "Invalid API key", status)
    return ValidationResult("xai", False, f"HTTP {status}", status)


# Registry of known validators
VALIDATORS: dict[str, callable] = {
    "openai": _validate_openai,
    "anthropic": _validate_anthropic,
    "gemini": _validate_gemini,
    "google": _validate_gemini,
    "github": _validate_github,
    "cloudflare": _validate_cloudflare,
    "cloudflare-api": _validate_cloudflare,
    "xai": _validate_xai,
}

# Map Keychain service patterns to validator names
SERVICE_PATTERNS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "github": "github",
    "cloudflare": "cloudflare",
    "xai": "xai",
}


def validate_key(provider: str, value: str) -> ValidationResult:
    """Validate an API key for a known provider.

    Args:
        provider: Provider name (e.g. "openai", "anthropic").
                  Also accepts partial matches (e.g. "claude-mcp-openai").
        value: The API key value to validate.

    Returns:
        ValidationResult with valid=True/False.
        If provider has no validator, returns valid=True with "no validator" message.
    """
    # Direct match
    validator = VALIDATORS.get(provider.lower())
    if validator:
        return validator(value)

    # Pattern match (for keychain service names like "claude-mcp-openai")
    provider_lower = provider.lower()
    for pattern, name in SERVICE_PATTERNS.items():
        if pattern in provider_lower:
            return VALIDATORS[name](value)

    return ValidationResult(provider, True, "No validator available (assumed valid)")


def list_supported_providers() -> list[str]:
    """List providers that have validation support."""
    return sorted(set(SERVICE_PATTERNS.values()))

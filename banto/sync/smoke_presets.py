# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Allowlisted smoke-test presets for sync-managed secret rotations."""
from __future__ import annotations

import json
from dataclasses import dataclass
import urllib.error
import urllib.request

from .capabilities import SecretClassification
from .validate import validate_key


@dataclass(frozen=True)
class SmokePresetDefinition:
    """Definition of one built-in smoke preset."""

    name: str
    description: str


SMOKE_PRESETS: dict[str, SmokePresetDefinition] = {
    "provider-validate": SmokePresetDefinition(
        name="provider-validate",
        description="Re-run the provider's lightweight validation after propagation.",
    ),
    "env-present": SmokePresetDefinition(
        name="env-present",
        description="Confirm that the resolved secret value is non-empty.",
    ),
    "openai-runtime": SmokePresetDefinition(
        name="openai-runtime",
        description="OpenAI only: verify moderations and chat completions, not just model listing.",
    ),
}


def list_smoke_presets() -> list[SmokePresetDefinition]:
    """Return allowlisted smoke presets in stable order."""
    return [SMOKE_PRESETS[name] for name in sorted(SMOKE_PRESETS)]


def smoke_preset_exists(name: str) -> bool:
    """Check whether a preset name is allowlisted."""
    return name in SMOKE_PRESETS


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict | None = None,
    timeout: int = 20,
) -> tuple[int, str]:
    """Minimal HTTP request helper for smoke presets."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace") if exc.fp else ""
    except (urllib.error.URLError, OSError) as exc:
        return 0, type(exc).__name__


def _extract_error_message(body: str) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:160]

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return body[:160]


def _run_openai_runtime_smoke(value: str) -> tuple[bool, str]:
    headers = {
        "Authorization": f"Bearer {value}",
        "Content-Type": "application/json",
    }
    moderation_status, moderation_body = _http_request(
        "POST",
        "https://api.openai.com/v1/moderations",
        headers=headers,
        payload={"input": "banto runtime smoke"},
    )
    chat_status, chat_body = _http_request(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        payload={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "Return OK."},
            ],
            "max_tokens": 5,
        },
    )

    if moderation_status == 200 and chat_status == 200:
        return True, "OpenAI moderations and chat completions succeeded."

    details: list[str] = []
    if moderation_status != 200:
        details.append(
            f"moderations={moderation_status or 'connection_failed'}:{_extract_error_message(moderation_body)}"
        )
    if chat_status != 200:
        details.append(
            f"chat={chat_status or 'connection_failed'}:{_extract_error_message(chat_body)}"
        )
    return False, "; ".join(details) or "OpenAI runtime smoke failed."


def run_smoke_preset(
    preset_name: str,
    *,
    classification: SecretClassification,
    value: str,
) -> tuple[bool, str]:
    """Run a built-in smoke preset and return (success, message)."""
    if preset_name == "env-present":
        if value:
            return True, "Secret value is present."
        return False, "Secret value is empty."

    if preset_name == "provider-validate":
        result = validate_key(classification.provider, value)
        if result.status == "pass":
            return True, result.message
        if result.status == "fail":
            return False, result.message
        return False, f"Validation inconclusive: {result.message}"

    if preset_name == "openai-runtime":
        if classification.provider != "openai":
            return False, "openai-runtime preset only supports OpenAI secrets."
        return _run_openai_runtime_smoke(value)

    raise ValueError(f"Unknown smoke preset: {preset_name}")

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Cloudflare Account API token issuance for sync-managed secrets."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..keychain import KeychainStore
from .config import SyncConfig
from .propagation import PropagationPlan, PropagationResult, build_propagation_plan, propagate_secret

DEFAULT_CREATOR_TOKEN_ENV = "CLOUDFLARE_TOKEN_CREATOR_API_TOKEN"
DEFAULT_CREATOR_ACCOUNT = "cloudflare-token-creator"
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareTokenRotatorError(RuntimeError):
    """Raised when Cloudflare token issuance cannot proceed safely."""


@dataclass(frozen=True)
class CloudflareAccountTokenPlan:
    """Plan for creating one Cloudflare Account API token."""

    propagation_plan: PropagationPlan
    account_id: str
    token_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CreatedCloudflareToken:
    """Created Cloudflare token metadata plus the unredacted token value."""

    token_id: str | None
    token_name: str
    token_value: str = field(repr=False)
    expires_on: str | None = None


@dataclass(frozen=True)
class DeletedCloudflareToken:
    """Outcome of a Cloudflare token deletion attempt."""

    token_id: str | None
    deleted: bool
    message: str = ""


@dataclass(frozen=True)
class CloudflareTokenRotationResult:
    """End-to-end result without exposing the Cloudflare token value."""

    plan: CloudflareAccountTokenPlan
    creator_token_source: str
    created: CreatedCloudflareToken | None
    propagation: PropagationResult | None
    cleanup_of_created_token: DeletedCloudflareToken | None = None
    revoked_previous_token: DeletedCloudflareToken | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.propagation is None or not self.propagation.ok:
            return False
        if self.revoked_previous_token is not None and not self.revoked_previous_token.deleted:
            return False
        return True


def default_cloudflare_token_name(secret_name: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%SZ").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", secret_name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug) or "secret"
    return f"banto-{slug}-{timestamp}"


def load_cloudflare_token_policy(path: Path | str) -> dict[str, Any]:
    """Load a Cloudflare token payload or policy list from JSON."""
    policy_path = Path(path)
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CloudflareTokenRotatorError(f"Failed to load Cloudflare policy JSON: {exc}") from exc

    if isinstance(raw, list):
        payload: dict[str, Any] = {"policies": raw}
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        raise CloudflareTokenRotatorError("Cloudflare policy JSON must be an object or array.")

    policies = payload.get("policies")
    if not isinstance(policies, list) or not policies:
        raise CloudflareTokenRotatorError("Cloudflare token payload requires a non-empty policies array.")
    return payload


def build_cloudflare_account_token_plan(
    config: SyncConfig,
    secret_name: str,
    account_id: str,
    payload: dict[str, Any],
    *,
    token_name: str | None = None,
) -> CloudflareAccountTokenPlan:
    """Validate that a secret can be issued via the Cloudflare token API."""
    if not account_id.strip():
        raise ValueError("Cloudflare account_id is required.")

    propagation_plan = build_propagation_plan(config, secret_name)
    if propagation_plan.provider != "cloudflare":
        raise ValueError(f"{propagation_plan.env_name} is not a Cloudflare API token.")
    if propagation_plan.rotation_class not in {"full_auto", "partial_auto", "propagate_only"}:
        raise ValueError(
            f"{propagation_plan.env_name} is classified as "
            f"{propagation_plan.rotation_class}; Cloudflare token issuance is not allowed."
        )

    resolved_payload = dict(payload)
    resolved_payload["name"] = (
        token_name.strip()
        if token_name and token_name.strip()
        else resolved_payload.get("name") or default_cloudflare_token_name(secret_name)
    )
    if not isinstance(resolved_payload.get("name"), str) or not resolved_payload["name"].strip():
        raise ValueError("Cloudflare token name is required.")

    return CloudflareAccountTokenPlan(
        propagation_plan=propagation_plan,
        account_id=account_id.strip(),
        token_name=resolved_payload["name"].strip(),
        payload=resolved_payload,
    )


def resolve_cloudflare_creator_token(
    config: SyncConfig,
    *,
    env_var: str = DEFAULT_CREATOR_TOKEN_ENV,
    account: str = DEFAULT_CREATOR_ACCOUNT,
) -> tuple[str, str]:
    """Resolve the Cloudflare token-creator credential from env or Keychain."""
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value, f"env:{env_var}"

    if account:
        kc = KeychainStore(service_prefix=config.keychain_service)
        value = kc.get(account)
        if value:
            return value, f"keychain:{config.keychain_service}:{account}"

    raise CloudflareTokenRotatorError(
        "Cloudflare token-creator credential not found. "
        f"Set ${env_var} or store Keychain account '{account}'."
    )


def _error_message_from_body(body: str) -> str:
    if not body:
        return "No response body"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and isinstance(first.get("message"), str):
            return first["message"]
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return body[:200]


def _cloudflare_request_json(
    method: str,
    path: str,
    *,
    token: str,
    payload: dict | None = None,
    timeout: int = 30,
) -> dict:
    url = f"{CLOUDFLARE_API_BASE}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "banto-sync",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise CloudflareTokenRotatorError(
            f"Cloudflare API {method} {path} failed with HTTP {exc.code}: "
            f"{_error_message_from_body(raw)}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise CloudflareTokenRotatorError(
            f"Cloudflare API {method} {path} failed: {type(exc).__name__}"
        ) from exc

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise CloudflareTokenRotatorError(
            f"Cloudflare API {method} {path} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise CloudflareTokenRotatorError(
            f"Cloudflare API {method} {path} returned unexpected payload type."
        )
    return parsed


def create_cloudflare_account_token(
    plan: CloudflareAccountTokenPlan,
    *,
    creator_token: str,
    timeout: int = 30,
) -> CreatedCloudflareToken:
    """Create a Cloudflare Account API token and return its one-time token value."""
    safe_account_id = urllib.parse.quote(plan.account_id, safe="")
    response = _cloudflare_request_json(
        "POST",
        f"/accounts/{safe_account_id}/tokens",
        token=creator_token,
        payload=plan.payload,
        timeout=timeout,
    )

    if response.get("success") is False:
        raise CloudflareTokenRotatorError(_error_message_from_body(json.dumps(response)))

    result = response.get("result")
    if not isinstance(result, dict):
        raise CloudflareTokenRotatorError("Cloudflare create-token response did not include result.")

    token_value = result.get("value") or result.get("token")
    if not isinstance(token_value, str) or not token_value:
        raise CloudflareTokenRotatorError(
            "Cloudflare create-token response did not include a one-time token value."
        )

    token_id = result.get("id")
    token_name = result.get("name")
    expires_on = result.get("expires_on")
    return CreatedCloudflareToken(
        token_id=token_id if isinstance(token_id, str) else None,
        token_name=token_name if isinstance(token_name, str) and token_name else plan.token_name,
        token_value=token_value,
        expires_on=expires_on if isinstance(expires_on, str) else None,
    )


def delete_cloudflare_account_token(
    account_id: str,
    token_id: str,
    *,
    creator_token: str,
    timeout: int = 30,
) -> DeletedCloudflareToken:
    """Delete a Cloudflare Account API token by id."""
    safe_account_id = urllib.parse.quote(account_id, safe="")
    safe_token_id = urllib.parse.quote(token_id, safe="")
    response = _cloudflare_request_json(
        "DELETE",
        f"/accounts/{safe_account_id}/tokens/{safe_token_id}",
        token=creator_token,
        timeout=timeout,
    )
    if response.get("success") is False:
        raise CloudflareTokenRotatorError(_error_message_from_body(json.dumps(response)))
    result = response.get("result")
    deleted = response.get("success") is True
    if isinstance(result, dict) and isinstance(result.get("id"), str):
        token_id = result["id"]
    return DeletedCloudflareToken(token_id=token_id, deleted=deleted)


def _safe_delete_cloudflare_account_token(
    account_id: str,
    token_id: str | None,
    *,
    creator_token: str,
) -> DeletedCloudflareToken:
    if not token_id:
        return DeletedCloudflareToken(
            token_id=None,
            deleted=False,
            message="Cloudflare token id was not available.",
        )
    try:
        return delete_cloudflare_account_token(
            account_id,
            token_id,
            creator_token=creator_token,
        )
    except CloudflareTokenRotatorError as exc:
        return DeletedCloudflareToken(
            token_id=token_id,
            deleted=False,
            message=str(exc),
        )


def rotate_cloudflare_account_token(
    config: SyncConfig,
    secret_name: str,
    account_id: str,
    payload: dict[str, Any],
    *,
    token_name: str | None = None,
    creator_token_env: str = DEFAULT_CREATOR_TOKEN_ENV,
    creator_account: str = DEFAULT_CREATOR_ACCOUNT,
    revoke_token_id: str | None = None,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
) -> CloudflareTokenRotationResult:
    """Create a Cloudflare token and propagate it through banto."""
    plan = build_cloudflare_account_token_plan(
        config,
        secret_name,
        account_id,
        payload,
        token_name=token_name,
    )
    creator_token, creator_source = resolve_cloudflare_creator_token(
        config,
        env_var=creator_token_env,
        account=creator_account,
    )
    created = create_cloudflare_account_token(plan, creator_token=creator_token)
    propagation = propagate_secret(
        config,
        secret_name,
        created.token_value,
        do_validate=do_validate,
        smoke_command=smoke_command,
        smoke_preset=smoke_preset,
    )
    if not propagation.ok:
        cleanup = _safe_delete_cloudflare_account_token(
            plan.account_id,
            created.token_id,
            creator_token=creator_token,
        )
        error = "Propagation failed after creating Cloudflare token."
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return CloudflareTokenRotationResult(
            plan=plan,
            creator_token_source=creator_source,
            created=created,
            propagation=propagation,
            cleanup_of_created_token=cleanup,
            error=error,
        )

    revoked_previous = None
    error = None
    if revoke_token_id:
        revoked_previous = _safe_delete_cloudflare_account_token(
            plan.account_id,
            revoke_token_id,
            creator_token=creator_token,
        )
        if not revoked_previous.deleted:
            error = f"Previous Cloudflare token revoke failed: {revoked_previous.message}"

    return CloudflareTokenRotationResult(
        plan=plan,
        creator_token_source=creator_source,
        created=created,
        propagation=propagation,
        revoked_previous_token=revoked_previous,
        error=error,
    )

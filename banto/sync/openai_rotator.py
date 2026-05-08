# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""OpenAI service-account rotation for `OPENAI_API_KEY`.

This module implements the first phase-3 `full_auto` rotator:
create a new OpenAI project service account, propagate its unredacted API key
through the shared Keychain/Vercel flow, and optionally retire the previous
service account after the new key is live.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..keychain import KeychainStore
from .config import SyncConfig
from .propagation import (
    PropagationPlan,
    PropagationResult,
    build_propagation_plan,
    propagate_secret,
)

DEFAULT_ADMIN_KEY_ENV = "OPENAI_ADMIN_KEY"
DEFAULT_ADMIN_ACCOUNT = "openai-admin"
OPENAI_API_BASE = "https://api.openai.com/v1"


class OpenAIRotatorError(RuntimeError):
    """Raised when OpenAI service-account rotation cannot proceed safely."""


@dataclass(frozen=True)
class OpenAIServiceAccountRotationPlan:
    """Plan for rotating one OpenAI sync-managed secret."""

    propagation_plan: PropagationPlan
    project_id: str
    service_account_name: str


@dataclass(frozen=True)
class CreatedOpenAIServiceAccount:
    """Created OpenAI service account plus the unredacted API key."""

    service_account_id: str
    service_account_name: str
    api_key_id: str | None
    api_key_value: str
    created_at: int | None = None


@dataclass(frozen=True)
class DeletedOpenAIServiceAccount:
    """Outcome of a service-account deletion attempt."""

    service_account_id: str
    deleted: bool
    message: str = ""


@dataclass(frozen=True)
class OpenAIServiceAccountSummary:
    """Metadata for one OpenAI project service account."""

    service_account_id: str
    service_account_name: str
    role: str | None = None
    created_at: int | None = None


@dataclass(frozen=True)
class OpenAIRollbackResult:
    """Best-effort rollback of the previous secret value."""

    attempted: bool
    restored_previous_value: bool
    version: int | None = None
    sync_ok: bool | None = None
    message: str = ""


@dataclass(frozen=True)
class OpenAIRotationResult:
    """End-to-end result of OpenAI service-account rotation."""

    plan: OpenAIServiceAccountRotationPlan
    admin_key_source: str
    created: CreatedOpenAIServiceAccount | None
    propagation: PropagationResult | None
    rollback: OpenAIRollbackResult | None = None
    cleanup_of_created_service_account: DeletedOpenAIServiceAccount | None = None
    revoked_previous_service_account: DeletedOpenAIServiceAccount | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.propagation is None or not self.propagation.ok:
            return False
        if (
            self.revoked_previous_service_account is not None
            and not self.revoked_previous_service_account.deleted
        ):
            return False
        return True


def default_service_account_name(
    secret_name: str,
    *,
    now: datetime | None = None,
) -> str:
    """Generate a deterministic, ASCII-safe service-account name."""
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%SZ").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", secret_name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug) or "secret"
    base = f"banto-{slug}"
    return f"{base[:48]}-{timestamp}"


def build_openai_service_account_plan(
    config: SyncConfig,
    secret_name: str,
    project_id: str,
    *,
    service_account_name: str | None = None,
) -> OpenAIServiceAccountRotationPlan:
    """Validate that a secret is eligible for full-auto OpenAI rotation."""
    if not project_id.strip():
        raise ValueError("OpenAI project_id is required.")

    propagation_plan = build_propagation_plan(config, secret_name)
    if propagation_plan.provider != "openai":
        raise ValueError(f"{propagation_plan.env_name} is not an OpenAI secret.")
    if propagation_plan.rotation_class != "full_auto":
        raise ValueError(
            f"{propagation_plan.env_name} is classified as "
            f"{propagation_plan.rotation_class}; OpenAI full-auto rotation only "
            "supports full_auto secrets."
        )

    resolved_name = (
        service_account_name.strip()
        if service_account_name and service_account_name.strip()
        else default_service_account_name(secret_name)
    )
    return OpenAIServiceAccountRotationPlan(
        propagation_plan=propagation_plan,
        project_id=project_id.strip(),
        service_account_name=resolved_name,
    )


def resolve_openai_admin_key(
    config: SyncConfig,
    *,
    env_var: str = DEFAULT_ADMIN_KEY_ENV,
    account: str = DEFAULT_ADMIN_ACCOUNT,
) -> tuple[str, str]:
    """Resolve the OpenAI admin key from env first, then Keychain."""
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value, f"env:{env_var}"

    if account:
        kc = KeychainStore(service_prefix=config.keychain_service)
        value = kc.get(account)
        if value:
            return value, f"keychain:{config.keychain_service}:{account}"

    raise OpenAIRotatorError(
        "OpenAI admin key not found. "
        f"Set ${env_var} or store Keychain account '{account}'."
    )


def _error_message_from_body(body: str) -> str:
    if not body:
        return "No response body"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return body[:200]


def _openai_request_json(
    method: str,
    path: str,
    *,
    admin_key: str,
    payload: dict | None = None,
    timeout: int = 30,
) -> dict:
    url = f"{OPENAI_API_BASE}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {admin_key}",
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
        raise OpenAIRotatorError(
            f"OpenAI API {method} {path} failed with HTTP {exc.code}: "
            f"{_error_message_from_body(raw)}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise OpenAIRotatorError(
            f"OpenAI API {method} {path} failed: {type(exc).__name__}"
        ) from exc

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAIRotatorError(
            f"OpenAI API {method} {path} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenAIRotatorError(
            f"OpenAI API {method} {path} returned unexpected payload type."
        )
    return parsed


def create_project_service_account(
    project_id: str,
    service_account_name: str,
    *,
    admin_key: str,
    timeout: int = 30,
) -> CreatedOpenAIServiceAccount:
    """Create an OpenAI project service account and return its API key."""
    safe_project_id = urllib.parse.quote(project_id, safe="")
    payload = _openai_request_json(
        "POST",
        f"/organization/projects/{safe_project_id}/service_accounts",
        admin_key=admin_key,
        payload={"name": service_account_name},
        timeout=timeout,
    )

    api_key = payload.get("api_key") if isinstance(payload.get("api_key"), dict) else {}
    service_account = (
        payload.get("service_account")
        if isinstance(payload.get("service_account"), dict)
        else {}
    )
    service_account_id = (
        payload.get("id")
        or service_account.get("id")
    )
    api_key_value = (
        api_key.get("value")
        or api_key.get("api_key")
        or payload.get("unredacted_api_key")
    )
    api_key_id = api_key.get("id") or payload.get("api_key_id")

    if not isinstance(service_account_id, str) or not service_account_id:
        raise OpenAIRotatorError(
            "OpenAI create service-account response did not include an id."
        )
    if not isinstance(api_key_value, str) or not api_key_value:
        raise OpenAIRotatorError(
            "OpenAI create service-account response did not include an unredacted API key."
        )

    return CreatedOpenAIServiceAccount(
        service_account_id=service_account_id,
        service_account_name=(
            payload.get("name")
            or service_account.get("name")
            or service_account_name
        ),
        api_key_id=api_key_id if isinstance(api_key_id, str) else None,
        api_key_value=api_key_value,
        created_at=payload.get("created_at")
        if isinstance(payload.get("created_at"), int)
        else None,
    )


def list_project_service_accounts(
    project_id: str,
    *,
    admin_key: str,
    limit: int = 100,
    timeout: int = 30,
) -> list[OpenAIServiceAccountSummary]:
    """List service accounts for one OpenAI project."""
    safe_project_id = urllib.parse.quote(project_id, safe="")
    bounded_limit = min(max(limit, 1), 100)
    payload = _openai_request_json(
        "GET",
        f"/organization/projects/{safe_project_id}/service_accounts?limit={bounded_limit}",
        admin_key=admin_key,
        timeout=timeout,
    )

    data = payload.get("data")
    if not isinstance(data, list):
        raise OpenAIRotatorError(
            "OpenAI list service-accounts response did not include a data array."
        )

    summaries: list[OpenAIServiceAccountSummary] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        service_account_id = item.get("id")
        if not isinstance(service_account_id, str) or not service_account_id:
            continue
        service_account_name = item.get("name")
        role = item.get("role")
        created_at = item.get("created_at")
        summaries.append(OpenAIServiceAccountSummary(
            service_account_id=service_account_id,
            service_account_name=(
                service_account_name
                if isinstance(service_account_name, str) and service_account_name
                else "(unnamed)"
            ),
            role=role if isinstance(role, str) and role else None,
            created_at=created_at if isinstance(created_at, int) else None,
        ))
    return summaries


def delete_project_service_account(
    project_id: str,
    service_account_id: str,
    *,
    admin_key: str,
    timeout: int = 30,
) -> DeletedOpenAIServiceAccount:
    """Delete an OpenAI project service account."""
    safe_project_id = urllib.parse.quote(project_id, safe="")
    safe_service_account_id = urllib.parse.quote(service_account_id, safe="")
    payload = _openai_request_json(
        "DELETE",
        f"/organization/projects/{safe_project_id}/service_accounts/{safe_service_account_id}",
        admin_key=admin_key,
        timeout=timeout,
    )
    deleted = payload.get("deleted") is True
    if not deleted:
        raise OpenAIRotatorError(
            f"OpenAI delete service-account response did not confirm deletion for {service_account_id}."
        )
    return DeletedOpenAIServiceAccount(
        service_account_id=service_account_id,
        deleted=True,
    )


def _safe_delete_project_service_account(
    project_id: str,
    service_account_id: str,
    *,
    admin_key: str,
    timeout: int = 30,
) -> DeletedOpenAIServiceAccount:
    try:
        return delete_project_service_account(
            project_id,
            service_account_id,
            admin_key=admin_key,
            timeout=timeout,
        )
    except OpenAIRotatorError as exc:
        return DeletedOpenAIServiceAccount(
            service_account_id=service_account_id,
            deleted=False,
            message=str(exc),
        )


def _rollback_previous_value(
    config: SyncConfig,
    secret_name: str,
    previous_value: str | None,
) -> OpenAIRollbackResult:
    if previous_value is None:
        return OpenAIRollbackResult(
            attempted=False,
            restored_previous_value=False,
            message="No previous Keychain value was available for rollback.",
        )

    restored = propagate_secret(
        config,
        secret_name,
        previous_value,
        do_validate=False,
        smoke_command=None,
        smoke_preset=None,
    )
    return OpenAIRollbackResult(
        attempted=True,
        restored_previous_value=restored.ok,
        version=restored.version,
        sync_ok=restored.sync_report.all_ok if restored.sync_report is not None else None,
        message=(
            "Previous value restored."
            if restored.ok
            else "Failed to restore previous value."
        ),
    )


def rotate_openai_service_account(
    config: SyncConfig,
    secret_name: str,
    project_id: str,
    *,
    service_account_name: str | None = None,
    admin_key_env: str = DEFAULT_ADMIN_KEY_ENV,
    admin_account: str = DEFAULT_ADMIN_ACCOUNT,
    revoke_service_account_id: str | None = None,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
) -> OpenAIRotationResult:
    """Rotate an OpenAI secret via project service-account issuance."""
    plan = build_openai_service_account_plan(
        config,
        secret_name,
        project_id,
        service_account_name=service_account_name,
    )
    admin_key, admin_key_source = resolve_openai_admin_key(
        config,
        env_var=admin_key_env,
        account=admin_account,
    )

    kc = KeychainStore(service_prefix=config.keychain_service)
    previous_value = kc.get(plan.propagation_plan.account)

    created = create_project_service_account(
        plan.project_id,
        plan.service_account_name,
        admin_key=admin_key,
    )

    propagation = propagate_secret(
        config,
        secret_name,
        created.api_key_value,
        do_validate=do_validate,
        smoke_command=smoke_command,
        smoke_preset=smoke_preset,
    )

    if not propagation.ok:
        rollback = _rollback_previous_value(config, secret_name, previous_value)
        cleanup = _safe_delete_project_service_account(
            plan.project_id,
            created.service_account_id,
            admin_key=admin_key,
        )
        error = "Propagation failed after creating a new OpenAI service account."
        if not rollback.restored_previous_value:
            error += f" {rollback.message}"
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return OpenAIRotationResult(
            plan=plan,
            admin_key_source=admin_key_source,
            created=created,
            propagation=propagation,
            rollback=rollback,
            cleanup_of_created_service_account=cleanup,
            error=error,
        )

    revoked = None
    error = None
    if revoke_service_account_id:
        revoked = _safe_delete_project_service_account(
            plan.project_id,
            revoke_service_account_id,
            admin_key=admin_key,
        )
        if not revoked.deleted:
            error = (
                "New key propagated, but the previous OpenAI service account "
                f"could not be revoked: {revoked.message}"
            )

    return OpenAIRotationResult(
        plan=plan,
        admin_key_source=admin_key_source,
        created=created,
        propagation=propagation,
        revoked_previous_service_account=revoked,
        error=error,
    )

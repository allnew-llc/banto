# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""xAI Management API rotation for sync-managed `XAI_API_KEY` secrets."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..keychain import KeychainStore
from .config import SyncConfig
from .propagation import PropagationPlan, PropagationResult, build_propagation_plan, propagate_secret

DEFAULT_MANAGEMENT_KEY_ENV = "XAI_MANAGEMENT_API_KEY"
DEFAULT_MANAGEMENT_ACCOUNT = "xai-management"
DEFAULT_XAI_ACLS = ("api-key:model:*", "api-key:endpoint:*")
XAI_MANAGEMENT_API_BASE = "https://management-api.x.ai"


class XAIRotatorError(RuntimeError):
    """Raised when xAI API key rotation cannot proceed safely."""


@dataclass(frozen=True)
class XAIAPIKeyRotationPlan:
    """Plan for rotating one xAI API key."""

    propagation_plan: PropagationPlan
    team_id: str
    key_name: str
    acls: tuple[str, ...]
    qps: int | None = None
    qpm: int | None = None
    tpm: str | None = None


@dataclass(frozen=True)
class CreatedXAIAPIKey:
    """Created xAI API key plus the unredacted key string."""

    api_key_id: str
    api_key_value: str
    name: str
    redacted_api_key: str | None = None
    create_time: str | None = None


@dataclass(frozen=True)
class DeletedXAIAPIKey:
    """Outcome of an xAI API key deletion attempt."""

    api_key_id: str
    deleted: bool
    message: str = ""


@dataclass(frozen=True)
class XAIPropagationStatus:
    """Propagation status across xAI inference clusters."""

    api_key_id: str
    propagated: bool
    clusters: dict[str, bool]
    message: str = ""


@dataclass(frozen=True)
class XAIRollbackResult:
    """Best-effort rollback of the previous secret value."""

    attempted: bool
    restored_previous_value: bool
    version: int | None = None
    sync_ok: bool | None = None
    message: str = ""


@dataclass(frozen=True)
class XAIAPIKeyRotationResult:
    """End-to-end result of xAI API key rotation."""

    plan: XAIAPIKeyRotationPlan
    management_key_source: str
    created: CreatedXAIAPIKey | None
    propagation: PropagationResult | None
    propagation_status: XAIPropagationStatus | None = None
    rollback: XAIRollbackResult | None = None
    cleanup_of_created_key: DeletedXAIAPIKey | None = None
    revoked_previous_key: DeletedXAIAPIKey | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.propagation_status is not None and not self.propagation_status.propagated:
            return False
        if self.propagation is None or not self.propagation.ok:
            return False
        if self.revoked_previous_key is not None and not self.revoked_previous_key.deleted:
            return False
        return True


def default_xai_key_name(secret_name: str, *, now: datetime | None = None) -> str:
    """Generate an ASCII-safe xAI API key name."""
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%SZ").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", secret_name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug) or "secret"
    return f"banto-{slug}-{timestamp}"


def build_xai_api_key_plan(
    config: SyncConfig,
    secret_name: str,
    team_id: str,
    *,
    key_name: str | None = None,
    acls: tuple[str, ...] | list[str] | None = None,
    qps: int | None = None,
    qpm: int | None = None,
    tpm: str | None = None,
) -> XAIAPIKeyRotationPlan:
    """Validate that a secret is eligible for xAI full-auto rotation."""
    if not team_id.strip():
        raise ValueError("xAI team_id is required.")

    propagation_plan = build_propagation_plan(config, secret_name)
    if propagation_plan.provider != "xai":
        raise ValueError(f"{propagation_plan.env_name} is not an xAI API key secret.")
    if propagation_plan.rotation_class != "full_auto":
        raise ValueError(
            f"{propagation_plan.env_name} is classified as "
            f"{propagation_plan.rotation_class}; xAI full-auto rotation only "
            "supports full_auto secrets."
        )

    resolved_acls = tuple(item.strip() for item in (acls or DEFAULT_XAI_ACLS) if item.strip())
    if not resolved_acls:
        raise ValueError("At least one xAI ACL is required.")

    return XAIAPIKeyRotationPlan(
        propagation_plan=propagation_plan,
        team_id=team_id.strip(),
        key_name=(
            key_name.strip()
            if key_name and key_name.strip()
            else default_xai_key_name(secret_name)
        ),
        acls=resolved_acls,
        qps=qps,
        qpm=qpm,
        tpm=tpm.strip() if tpm and tpm.strip() else None,
    )


def resolve_xai_management_key(
    config: SyncConfig,
    *,
    env_var: str = DEFAULT_MANAGEMENT_KEY_ENV,
    account: str = DEFAULT_MANAGEMENT_ACCOUNT,
) -> tuple[str, str]:
    """Resolve the xAI management key from env first, then Keychain."""
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value, f"env:{env_var}"

    if account:
        kc = KeychainStore(service_prefix=config.keychain_service)
        value = kc.get(account)
        if value:
            return value, f"keychain:{config.keychain_service}:{account}"

    raise XAIRotatorError(
        "xAI management key not found. "
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


def _xai_request_json(
    method: str,
    path: str,
    *,
    management_key: str,
    payload: dict | None = None,
    timeout: int = 30,
) -> dict:
    url = f"{XAI_MANAGEMENT_API_BASE}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {management_key}",
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
        raise XAIRotatorError(
            f"xAI Management API {method} {path} failed with HTTP {exc.code}: "
            f"{_error_message_from_body(raw)}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise XAIRotatorError(
            f"xAI Management API {method} {path} failed: {type(exc).__name__}"
        ) from exc

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise XAIRotatorError(
            f"xAI Management API {method} {path} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise XAIRotatorError(
            f"xAI Management API {method} {path} returned unexpected payload type."
        )
    return parsed


def create_xai_api_key(
    plan: XAIAPIKeyRotationPlan,
    *,
    management_key: str,
    timeout: int = 30,
) -> CreatedXAIAPIKey:
    """Create an xAI API key and return the unredacted key string."""
    safe_team_id = urllib.parse.quote(plan.team_id, safe="")
    payload: dict[str, object] = {
        "name": plan.key_name,
        "acls": list(plan.acls),
    }
    if plan.qps is not None:
        payload["qps"] = plan.qps
    if plan.qpm is not None:
        payload["qpm"] = plan.qpm
    if plan.tpm is not None:
        payload["tpm"] = plan.tpm

    response = _xai_request_json(
        "POST",
        f"/auth/teams/{safe_team_id}/api-keys",
        management_key=management_key,
        payload=payload,
        timeout=timeout,
    )

    api_key_id = response.get("apiKeyId")
    api_key_value = response.get("apiKey")
    if not isinstance(api_key_id, str) or not api_key_id:
        raise XAIRotatorError("xAI create-key response did not include apiKeyId.")
    if not isinstance(api_key_value, str) or not api_key_value:
        raise XAIRotatorError("xAI create-key response did not include apiKey.")

    name = response.get("name")
    redacted = response.get("redactedApiKey")
    create_time = response.get("createTime")
    return CreatedXAIAPIKey(
        api_key_id=api_key_id,
        api_key_value=api_key_value,
        name=name if isinstance(name, str) and name else plan.key_name,
        redacted_api_key=redacted if isinstance(redacted, str) else None,
        create_time=create_time if isinstance(create_time, str) else None,
    )


def get_xai_api_key_propagation(
    api_key_id: str,
    *,
    management_key: str,
    timeout: int = 30,
) -> XAIPropagationStatus:
    """Check if an xAI API key has propagated to inference clusters."""
    safe_api_key_id = urllib.parse.quote(api_key_id, safe="")
    response = _xai_request_json(
        "GET",
        f"/auth/api-keys/{safe_api_key_id}/propagation",
        management_key=management_key,
        timeout=timeout,
    )
    raw_clusters = response.get("icPropagation")
    clusters: dict[str, bool] = {}
    if isinstance(raw_clusters, dict):
        clusters = {
            str(name): bool(value)
            for name, value in raw_clusters.items()
        }
    propagated = bool(clusters) and all(clusters.values())
    return XAIPropagationStatus(
        api_key_id=api_key_id,
        propagated=propagated,
        clusters=clusters,
        message="" if propagated else "xAI API key has not propagated to all clusters.",
    )


def wait_for_xai_api_key_propagation(
    api_key_id: str,
    *,
    management_key: str,
    timeout: int = 90,
    poll_interval: float = 2.0,
) -> XAIPropagationStatus:
    """Wait until an xAI API key is available across inference clusters."""
    started = time.monotonic()
    latest: XAIPropagationStatus | None = None
    while True:
        latest = get_xai_api_key_propagation(
            api_key_id,
            management_key=management_key,
        )
        if latest.propagated:
            return latest
        if time.monotonic() - started > timeout:
            return XAIPropagationStatus(
                api_key_id=api_key_id,
                propagated=False,
                clusters=latest.clusters if latest is not None else {},
                message=f"xAI API key propagation timed out after {timeout}s.",
            )
        time.sleep(poll_interval)


def delete_xai_api_key(
    api_key_id: str,
    *,
    management_key: str,
    timeout: int = 30,
) -> DeletedXAIAPIKey:
    """Delete an xAI API key."""
    safe_api_key_id = urllib.parse.quote(api_key_id, safe="")
    _xai_request_json(
        "DELETE",
        f"/auth/api-keys/{safe_api_key_id}",
        management_key=management_key,
        timeout=timeout,
    )
    return DeletedXAIAPIKey(api_key_id=api_key_id, deleted=True)


def _safe_delete_xai_api_key(
    api_key_id: str,
    *,
    management_key: str,
    timeout: int = 30,
) -> DeletedXAIAPIKey:
    try:
        return delete_xai_api_key(
            api_key_id,
            management_key=management_key,
            timeout=timeout,
        )
    except XAIRotatorError as exc:
        return DeletedXAIAPIKey(
            api_key_id=api_key_id,
            deleted=False,
            message=str(exc),
        )


def _rollback_previous_value(
    config: SyncConfig,
    secret_name: str,
    previous_value: str | None,
) -> XAIRollbackResult:
    if previous_value is None:
        return XAIRollbackResult(
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
    return XAIRollbackResult(
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


def rotate_xai_api_key(
    config: SyncConfig,
    secret_name: str,
    team_id: str,
    *,
    key_name: str | None = None,
    acls: tuple[str, ...] | list[str] | None = None,
    qps: int | None = None,
    qpm: int | None = None,
    tpm: str | None = None,
    management_key_env: str = DEFAULT_MANAGEMENT_KEY_ENV,
    management_account: str = DEFAULT_MANAGEMENT_ACCOUNT,
    revoke_api_key_id: str | None = None,
    wait_for_propagation: bool = False,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
) -> XAIAPIKeyRotationResult:
    """Rotate an xAI API key via the xAI Management API."""
    plan = build_xai_api_key_plan(
        config,
        secret_name,
        team_id,
        key_name=key_name,
        acls=acls,
        qps=qps,
        qpm=qpm,
        tpm=tpm,
    )
    management_key, management_key_source = resolve_xai_management_key(
        config,
        env_var=management_key_env,
        account=management_account,
    )

    kc = KeychainStore(service_prefix=config.keychain_service)
    previous_value = kc.get(plan.propagation_plan.account)
    created = create_xai_api_key(plan, management_key=management_key)

    propagation_status = None
    if wait_for_propagation:
        propagation_status = wait_for_xai_api_key_propagation(
            created.api_key_id,
            management_key=management_key,
        )
        if not propagation_status.propagated:
            cleanup = _safe_delete_xai_api_key(
                created.api_key_id,
                management_key=management_key,
            )
            error = propagation_status.message
            if not cleanup.deleted:
                error += f" Cleanup failed: {cleanup.message}"
            return XAIAPIKeyRotationResult(
                plan=plan,
                management_key_source=management_key_source,
                created=created,
                propagation=None,
                propagation_status=propagation_status,
                cleanup_of_created_key=cleanup,
                error=error,
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
        cleanup = _safe_delete_xai_api_key(
            created.api_key_id,
            management_key=management_key,
        )
        error = "Propagation failed after creating a new xAI API key."
        if not rollback.restored_previous_value:
            error += f" {rollback.message}"
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return XAIAPIKeyRotationResult(
            plan=plan,
            management_key_source=management_key_source,
            created=created,
            propagation=propagation,
            propagation_status=propagation_status,
            rollback=rollback,
            cleanup_of_created_key=cleanup,
            error=error,
        )

    revoked = None
    error = None
    if revoke_api_key_id:
        revoked = _safe_delete_xai_api_key(
            revoke_api_key_id,
            management_key=management_key,
        )
        if not revoked.deleted:
            error = (
                "New key propagated, but the previous xAI API key "
                f"could not be revoked: {revoked.message}"
            )

    return XAIAPIKeyRotationResult(
        plan=plan,
        management_key_source=management_key_source,
        created=created,
        propagation=propagation,
        propagation_status=propagation_status,
        revoked_previous_key=revoked,
        error=error,
    )

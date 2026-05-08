# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Google Cloud API key rotation for sync-managed `GOOGLE_API_KEY` secrets."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..keychain import KeychainStore
from .config import SyncConfig
from .propagation import PropagationPlan, PropagationResult, build_propagation_plan, propagate_secret

DEFAULT_ACCESS_TOKEN_ENV = "GOOGLE_OAUTH_ACCESS_TOKEN"
DEFAULT_ADC_COMMAND = "gcloud auth application-default print-access-token"
DEFAULT_GCLOUD_AUTH_COMMAND = "gcloud auth print-access-token"
GOOGLE_API_KEYS_BASE = "https://apikeys.googleapis.com/v2"

_KEY_ID_RE = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")


class GoogleRotatorError(RuntimeError):
    """Raised when Google API key rotation cannot proceed safely."""


@dataclass(frozen=True)
class GoogleAPIKeyRotationPlan:
    """Plan for rotating one Google API key."""

    propagation_plan: PropagationPlan
    project_id: str
    parent: str
    display_name: str
    key_id: str | None
    quota_project: str | None
    shared_account_secret_names: tuple[str, ...]


@dataclass(frozen=True)
class CreatedGoogleAPIKey:
    """Created Google API key plus the unredacted key string."""

    key_name: str
    display_name: str
    key_id: str | None
    key_uid: str | None
    key_string: str
    operation_name: str


@dataclass(frozen=True)
class DeletedGoogleAPIKey:
    """Outcome of a Google API key deletion attempt."""

    key_name: str
    deleted: bool
    operation_name: str | None = None
    message: str = ""


@dataclass(frozen=True)
class GoogleRollbackEntry:
    """Best-effort rollback for one propagated secret."""

    secret_name: str
    restored_previous_value: bool
    version: int | None = None
    sync_ok: bool | None = None
    message: str = ""


@dataclass(frozen=True)
class GoogleSiblingPropagationResult:
    """Result of syncing a sibling secret that shares the same Keychain account."""

    secret_name: str
    attempted: bool
    ok: bool
    skipped: bool = False
    reason: str = ""
    propagation: PropagationResult | None = None


@dataclass(frozen=True)
class GoogleAPIKeyRotationResult:
    """End-to-end result of Google API key rotation."""

    plan: GoogleAPIKeyRotationPlan
    access_token_source: str
    created: CreatedGoogleAPIKey | None
    primary_propagation: PropagationResult | None
    sibling_propagations: tuple[GoogleSiblingPropagationResult, ...] = ()
    rollback_entries: tuple[GoogleRollbackEntry, ...] = ()
    cleanup_of_created_key: DeletedGoogleAPIKey | None = None
    revoked_previous_key: DeletedGoogleAPIKey | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.primary_propagation is None or not self.primary_propagation.ok:
            return False
        if any(item.attempted and not item.ok for item in self.sibling_propagations):
            return False
        if self.revoked_previous_key is not None and not self.revoked_previous_key.deleted:
            return False
        return True


def default_google_display_name(secret_name: str, *, now: datetime | None = None) -> str:
    """Generate an ASCII-safe Google key display name."""
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%SZ").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", secret_name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug) or "secret"
    return f"banto-{slug}-{timestamp}"


def build_google_api_key_plan(
    config: SyncConfig,
    secret_name: str,
    project_id: str,
    *,
    display_name: str | None = None,
    key_id: str | None = None,
    quota_project: str | None = None,
) -> GoogleAPIKeyRotationPlan:
    """Validate that a secret is eligible for Google full-auto rotation."""
    if not project_id.strip():
        raise ValueError("Google project_id is required.")

    propagation_plan = build_propagation_plan(config, secret_name)
    if propagation_plan.provider != "google":
        raise ValueError(f"{propagation_plan.env_name} is not a Google API key secret.")
    if propagation_plan.rotation_class != "full_auto":
        raise ValueError(
            f"{propagation_plan.env_name} is classified as "
            f"{propagation_plan.rotation_class}; Google full-auto rotation only "
            "supports full_auto secrets."
        )

    if key_id is not None:
        key_id = key_id.strip()
        if not key_id:
            key_id = None
        elif not _KEY_ID_RE.match(key_id):
            raise ValueError(
                "Google key_id must match ^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$."
            )

    siblings = tuple(
        entry.name
        for entry in config.secrets.values()
        if entry.account == propagation_plan.account and entry.name != secret_name
    )

    return GoogleAPIKeyRotationPlan(
        propagation_plan=propagation_plan,
        project_id=project_id.strip(),
        parent=f"projects/{project_id.strip()}/locations/global",
        display_name=(
            display_name.strip()
            if display_name and display_name.strip()
            else default_google_display_name(secret_name)
        ),
        key_id=key_id,
        quota_project=quota_project.strip() if quota_project and quota_project.strip() else project_id.strip(),
        shared_account_secret_names=siblings,
    )


def resolve_google_access_token(
    *,
    env_var: str = DEFAULT_ACCESS_TOKEN_ENV,
    adc_command: str = DEFAULT_ADC_COMMAND,
    gcloud_auth_command: str = DEFAULT_GCLOUD_AUTH_COMMAND,
) -> tuple[str, str]:
    """Resolve a Google OAuth access token from env, ADC, then gcloud auth."""
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value, f"env:{env_var}"

    attempts = (
        ("ADC command", adc_command, "adc"),
        ("gcloud auth command", gcloud_auth_command, "gcloud"),
    )
    failures: list[str] = []

    for label, command, source_prefix in attempts:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            failures.append(f"Failed to parse {label.lower()}: {exc}")
            continue

        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            failures.append(f"{label} not found: {argv[0]}")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"{label} timed out (30s).")
            continue
        except OSError as exc:
            failures.append(f"{label} failed: {type(exc).__name__}")
            continue

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            failures.append(
                f"{label} failed (exit {result.returncode}): {stderr or 'no stderr'}"
            )
            continue

        token = result.stdout.strip()
        if not token:
            failures.append(f"{label} returned an empty access token.")
            continue

        return token, f"{source_prefix}:{command}"

    raise GoogleRotatorError(
        "Unable to resolve Google access token. Tried: " + " | ".join(failures)
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


def _google_request_json(
    method: str,
    path: str,
    *,
    access_token: str,
    payload: dict | None = None,
    quota_project: str | None = None,
    timeout: int = 30,
) -> dict:
    url = f"{GOOGLE_API_KEYS_BASE}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "banto-sync",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise GoogleRotatorError(
            f"Google API {method} {path} failed with HTTP {exc.code}: "
            f"{_error_message_from_body(raw)}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise GoogleRotatorError(
            f"Google API {method} {path} failed: {type(exc).__name__}"
        ) from exc

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoogleRotatorError(
            f"Google API {method} {path} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise GoogleRotatorError(
            f"Google API {method} {path} returned unexpected payload type."
        )
    return parsed


def _wait_for_operation(
    operation_name: str,
    *,
    access_token: str,
    quota_project: str | None,
    timeout: int = 90,
    poll_interval: float = 1.0,
) -> dict:
    started = time.monotonic()
    while True:
        operation = _google_request_json(
            "GET",
            f"/{operation_name}",
            access_token=access_token,
            quota_project=quota_project,
        )
        if operation.get("done") is True:
            error = operation.get("error")
            if isinstance(error, dict):
                message = error.get("message") or "operation failed"
                raise GoogleRotatorError(
                    f"Google operation {operation_name} failed: {message}"
                )
            return operation
        if time.monotonic() - started > timeout:
            raise GoogleRotatorError(
                f"Google operation {operation_name} timed out after {timeout}s."
            )
        time.sleep(poll_interval)


def create_google_api_key(
    parent: str,
    display_name: str,
    *,
    access_token: str,
    quota_project: str | None,
    key_id: str | None = None,
    timeout: int = 90,
) -> CreatedGoogleAPIKey:
    """Create a Google API key and return the unredacted key string."""
    query = ""
    if key_id:
        query = "?" + urllib.parse.urlencode({"keyId": key_id})
    operation = _google_request_json(
        "POST",
        f"/{parent}/keys{query}",
        access_token=access_token,
        quota_project=quota_project,
        payload={"displayName": display_name},
    )
    operation_name = operation.get("name")
    if not isinstance(operation_name, str) or not operation_name:
        raise GoogleRotatorError("Google create-key response did not include an operation name.")

    completed = _wait_for_operation(
        operation_name,
        access_token=access_token,
        quota_project=quota_project,
        timeout=timeout,
    )
    response = completed.get("response") if isinstance(completed.get("response"), dict) else {}
    metadata = completed.get("metadata") if isinstance(completed.get("metadata"), dict) else {}
    key_name = response.get("name") or metadata.get("target")
    if not isinstance(key_name, str) or not key_name:
        raise GoogleRotatorError("Google create-key operation did not reveal the key resource name.")

    key_string_response = _google_request_json(
        "GET",
        f"/{key_name}/keyString",
        access_token=access_token,
        quota_project=quota_project,
    )
    key_string = key_string_response.get("keyString")
    if not isinstance(key_string, str) or not key_string:
        raise GoogleRotatorError("Google getKeyString response did not include keyString.")

    return CreatedGoogleAPIKey(
        key_name=key_name,
        display_name=response.get("displayName") or display_name,
        key_id=response.get("keyId") if isinstance(response.get("keyId"), str) else key_id,
        key_uid=response.get("uid") if isinstance(response.get("uid"), str) else None,
        key_string=key_string,
        operation_name=operation_name,
    )


def delete_google_api_key(
    key_name: str,
    *,
    access_token: str,
    quota_project: str | None,
    timeout: int = 90,
) -> DeletedGoogleAPIKey:
    """Delete a Google API key and wait for completion."""
    operation = _google_request_json(
        "DELETE",
        f"/{key_name}",
        access_token=access_token,
        quota_project=quota_project,
    )
    operation_name = operation.get("name")
    if not isinstance(operation_name, str) or not operation_name:
        raise GoogleRotatorError("Google delete-key response did not include an operation name.")

    _wait_for_operation(
        operation_name,
        access_token=access_token,
        quota_project=quota_project,
        timeout=timeout,
    )
    return DeletedGoogleAPIKey(
        key_name=key_name,
        deleted=True,
        operation_name=operation_name,
    )


def _safe_delete_google_api_key(
    key_name: str,
    *,
    access_token: str,
    quota_project: str | None,
    timeout: int = 90,
) -> DeletedGoogleAPIKey:
    try:
        return delete_google_api_key(
            key_name,
            access_token=access_token,
            quota_project=quota_project,
            timeout=timeout,
        )
    except GoogleRotatorError as exc:
        return DeletedGoogleAPIKey(
            key_name=key_name,
            deleted=False,
            message=str(exc),
        )


def _rollback_secret_names(
    config: SyncConfig,
    secret_names: list[str],
    previous_value: str | None,
) -> tuple[GoogleRollbackEntry, ...]:
    if previous_value is None:
        return tuple(
            GoogleRollbackEntry(
                secret_name=name,
                restored_previous_value=False,
                message="No previous Keychain value was available for rollback.",
            )
            for name in secret_names
        )

    results: list[GoogleRollbackEntry] = []
    for name in secret_names:
        restored = propagate_secret(
            config,
            name,
            previous_value,
            do_validate=False,
            smoke_command=None,
            smoke_preset=None,
        )
        results.append(GoogleRollbackEntry(
            secret_name=name,
            restored_previous_value=restored.ok,
            version=restored.version,
            sync_ok=restored.sync_report.all_ok if restored.sync_report is not None else None,
            message=(
                "Previous value restored."
                if restored.ok
                else "Failed to restore previous value."
            ),
        ))
    return tuple(results)


def rotate_google_api_key(
    config: SyncConfig,
    secret_name: str,
    project_id: str,
    *,
    display_name: str | None = None,
    key_id: str | None = None,
    quota_project: str | None = None,
    access_token_env: str = DEFAULT_ACCESS_TOKEN_ENV,
    adc_command: str = DEFAULT_ADC_COMMAND,
    revoke_key_name: str | None = None,
    sync_shared_account_secrets: bool = False,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
) -> GoogleAPIKeyRotationResult:
    """Rotate a Google API key via the API Keys API."""
    plan = build_google_api_key_plan(
        config,
        secret_name,
        project_id,
        display_name=display_name,
        key_id=key_id,
        quota_project=quota_project,
    )
    access_token, access_token_source = resolve_google_access_token(
        env_var=access_token_env,
        adc_command=adc_command,
    )

    kc = KeychainStore(service_prefix=config.keychain_service)
    previous_value = kc.get(plan.propagation_plan.account)
    created = create_google_api_key(
        plan.parent,
        plan.display_name,
        access_token=access_token,
        quota_project=plan.quota_project,
        key_id=plan.key_id,
    )

    primary = propagate_secret(
        config,
        secret_name,
        created.key_string,
        do_validate=do_validate,
        smoke_command=smoke_command,
        smoke_preset=smoke_preset,
    )

    if not primary.ok:
        rollbacks = _rollback_secret_names(config, [secret_name], previous_value)
        cleanup = _safe_delete_google_api_key(
            created.key_name,
            access_token=access_token,
            quota_project=plan.quota_project,
        )
        error = "Propagation failed after creating a new Google API key."
        if not all(item.restored_previous_value for item in rollbacks):
            error += " Rollback did not fully restore the previous value."
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return GoogleAPIKeyRotationResult(
            plan=plan,
            access_token_source=access_token_source,
            created=created,
            primary_propagation=primary,
            rollback_entries=rollbacks,
            cleanup_of_created_key=cleanup,
            error=error,
        )

    sibling_results: list[GoogleSiblingPropagationResult] = []
    successful_siblings: list[str] = []
    if sync_shared_account_secrets:
        for sibling_name in plan.shared_account_secret_names:
            sibling_propagation = propagate_secret(
                config,
                sibling_name,
                created.key_string,
                do_validate=False,
                smoke_command=None,
                smoke_preset=None,
            )
            sibling_ok = sibling_propagation.ok
            sibling_results.append(GoogleSiblingPropagationResult(
                secret_name=sibling_name,
                attempted=True,
                ok=sibling_ok,
                propagation=sibling_propagation,
            ))
            if sibling_ok:
                successful_siblings.append(sibling_name)
            else:
                rollback_names = [secret_name] + successful_siblings
                rollbacks = _rollback_secret_names(config, rollback_names, previous_value)
                cleanup = _safe_delete_google_api_key(
                    created.key_name,
                    access_token=access_token,
                    quota_project=plan.quota_project,
                )
                error = (
                    "Shared-account propagation failed after the primary Google API key "
                    "was updated."
                )
                if not all(item.restored_previous_value for item in rollbacks):
                    error += " Rollback did not fully restore the previous value."
                if not cleanup.deleted:
                    error += f" Cleanup failed: {cleanup.message}"
                return GoogleAPIKeyRotationResult(
                    plan=plan,
                    access_token_source=access_token_source,
                    created=created,
                    primary_propagation=primary,
                    sibling_propagations=tuple(sibling_results),
                    rollback_entries=rollbacks,
                    cleanup_of_created_key=cleanup,
                    error=error,
                )
    else:
        sibling_results = [
            GoogleSiblingPropagationResult(
                secret_name=name,
                attempted=False,
                ok=True,
                skipped=True,
                reason="shared-account sync not requested",
            )
            for name in plan.shared_account_secret_names
        ]

    revoked = None
    error = None
    if revoke_key_name:
        revoked = _safe_delete_google_api_key(
            revoke_key_name,
            access_token=access_token,
            quota_project=plan.quota_project,
        )
        if not revoked.deleted:
            error = (
                "New key propagated, but the previous Google API key could not be revoked: "
                f"{revoked.message}"
            )

    return GoogleAPIKeyRotationResult(
        plan=plan,
        access_token_source=access_token_source,
        created=created,
        primary_propagation=primary,
        sibling_propagations=tuple(sibling_results),
        revoked_previous_key=revoked,
        error=error,
    )

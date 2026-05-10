# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Stripe webhook endpoint issuance for sync-managed webhook secrets."""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..keychain import KeychainStore
from .config import SyncConfig
from .propagation import PropagationPlan, PropagationResult, build_propagation_plan, propagate_secret

STRIPE_API_BASE = "https://api.stripe.com/v1"


class StripeWebhookRotatorError(RuntimeError):
    """Raised when Stripe webhook issuance cannot proceed safely."""


@dataclass(frozen=True)
class StripeWebhookEndpointPlan:
    """Plan for creating one Stripe webhook endpoint."""

    propagation_plan: PropagationPlan
    source_secret_name: str
    url: str
    enabled_events: tuple[str, ...]
    description: str | None = None
    api_version: str | None = None
    connect: bool = False


@dataclass(frozen=True)
class CreatedStripeWebhookEndpoint:
    """Created webhook endpoint metadata plus the unredacted signing secret."""

    endpoint_id: str
    signing_secret: str = field(repr=False)
    livemode: bool | None = None
    status: str | None = None


@dataclass(frozen=True)
class DeletedStripeWebhookEndpoint:
    """Outcome of deleting a Stripe webhook endpoint."""

    endpoint_id: str
    deleted: bool
    message: str = ""


@dataclass(frozen=True)
class StripeWebhookRotationResult:
    """End-to-end result without exposing the signing secret."""

    plan: StripeWebhookEndpointPlan
    stripe_key_source: str
    created: CreatedStripeWebhookEndpoint | None
    propagation: PropagationResult | None
    cleanup_of_created_endpoint: DeletedStripeWebhookEndpoint | None = None
    deleted_previous_endpoint: DeletedStripeWebhookEndpoint | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.propagation is None or not self.propagation.ok:
            return False
        if self.deleted_previous_endpoint is not None and not self.deleted_previous_endpoint.deleted:
            return False
        return True


def build_stripe_webhook_endpoint_plan(
    config: SyncConfig,
    secret_name: str,
    *,
    source_secret_name: str,
    url: str,
    enabled_events: tuple[str, ...] | list[str],
    description: str | None = None,
    api_version: str | None = None,
    connect: bool = False,
) -> StripeWebhookEndpointPlan:
    """Validate that a webhook secret can be issued through Stripe API."""
    if not source_secret_name.strip():
        raise ValueError("source_secret_name is required.")
    if not url.strip().startswith("https://"):
        raise ValueError("Stripe webhook endpoint URL must be https.")
    events = tuple(item.strip() for item in enabled_events if item.strip())
    if not events:
        raise ValueError("At least one Stripe webhook event is required.")

    propagation_plan = build_propagation_plan(config, secret_name)
    if propagation_plan.provider != "stripe" or propagation_plan.env_name != "STRIPE_WEBHOOK_SECRET":
        raise ValueError(f"{propagation_plan.env_name} is not a Stripe webhook secret.")
    if propagation_plan.rotation_class != "manual_cutover":
        raise ValueError(
            f"{propagation_plan.env_name} is classified as "
            f"{propagation_plan.rotation_class}; expected manual_cutover."
        )
    if config.get_secret(source_secret_name) is None:
        raise ValueError(f"Stripe source secret '{source_secret_name}' is not configured.")

    return StripeWebhookEndpointPlan(
        propagation_plan=propagation_plan,
        source_secret_name=source_secret_name.strip(),
        url=url.strip(),
        enabled_events=events,
        description=description.strip() if description and description.strip() else None,
        api_version=api_version.strip() if api_version and api_version.strip() else None,
        connect=connect,
    )


def resolve_stripe_api_key(config: SyncConfig, source_secret_name: str) -> tuple[str, str]:
    """Resolve the Stripe API key from an existing sync-managed secret account."""
    entry = config.get_secret(source_secret_name)
    if entry is None:
        raise StripeWebhookRotatorError(f"Stripe source secret '{source_secret_name}' is not configured.")
    kc = KeychainStore(service_prefix=config.keychain_service)
    value = kc.get(entry.account)
    if value:
        return value, f"keychain:{config.keychain_service}:{entry.account}"
    raise StripeWebhookRotatorError(
        f"Stripe API key for source secret '{source_secret_name}' was not found in Keychain."
    )


def _stripe_request_json(
    method: str,
    path: str,
    *,
    api_key: str,
    fields: list[tuple[str, str]],
    timeout: int = 30,
) -> dict:
    url = f"{STRIPE_API_BASE}{path}"
    encoded = urllib.parse.urlencode(fields).encode("utf-8") if fields else None
    basic = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "User-Agent": "banto-sync",
    }
    if encoded is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise StripeWebhookRotatorError(
            f"Stripe API {method} {path} failed with HTTP {exc.code}: "
            f"{_stripe_error_message(raw)}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise StripeWebhookRotatorError(
            f"Stripe API {method} {path} failed: {type(exc).__name__}"
        ) from exc

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise StripeWebhookRotatorError(
            f"Stripe API {method} {path} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise StripeWebhookRotatorError(
            f"Stripe API {method} {path} returned unexpected payload type."
        )
    return parsed


def _stripe_error_message(body: str) -> str:
    if not body:
        return "No response body"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return body[:200]


def _parse_stripe_cli_json(raw: str, *, command: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise StripeWebhookRotatorError(f"Stripe CLI {command} returned no JSON payload.")
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise StripeWebhookRotatorError(
            f"Stripe CLI {command} returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise StripeWebhookRotatorError(
            f"Stripe CLI {command} returned unexpected payload type."
        )
    return parsed


def _run_stripe_cli_json(argv: list[str], *, command: str, timeout: int = 30) -> dict:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise StripeWebhookRotatorError("Stripe CLI was not found on PATH.") from None
    except subprocess.TimeoutExpired as exc:
        raise StripeWebhookRotatorError(
            f"Stripe CLI {command} timed out ({timeout}s)."
        ) from exc
    if result.returncode != 0:
        raise StripeWebhookRotatorError(
            f"Stripe CLI {command} failed with exit {result.returncode}."
        )
    return _parse_stripe_cli_json(result.stdout, command=command)


def create_stripe_webhook_endpoint(
    plan: StripeWebhookEndpointPlan,
    *,
    api_key: str,
    timeout: int = 30,
) -> CreatedStripeWebhookEndpoint:
    """Create a Stripe webhook endpoint and return its one-time signing secret."""
    fields: list[tuple[str, str]] = [
        ("url", plan.url),
        ("description", plan.description or f"banto {plan.propagation_plan.secret_name} {datetime.now(timezone.utc).isoformat()}"),
    ]
    for event in plan.enabled_events:
        fields.append(("enabled_events[]", event))
    if plan.api_version:
        fields.append(("api_version", plan.api_version))
    if plan.connect:
        fields.append(("connect", "true"))

    response = _stripe_request_json(
        "POST",
        "/webhook_endpoints",
        api_key=api_key,
        fields=fields,
        timeout=timeout,
    )
    endpoint_id = response.get("id")
    signing_secret = response.get("secret")
    if not isinstance(endpoint_id, str) or not endpoint_id:
        raise StripeWebhookRotatorError("Stripe webhook response did not include an endpoint id.")
    if not isinstance(signing_secret, str) or not signing_secret:
        raise StripeWebhookRotatorError("Stripe webhook response did not include a signing secret.")
    livemode = response.get("livemode")
    status = response.get("status")
    return CreatedStripeWebhookEndpoint(
        endpoint_id=endpoint_id,
        signing_secret=signing_secret,
        livemode=livemode if isinstance(livemode, bool) else None,
        status=status if isinstance(status, str) else None,
    )


def create_stripe_webhook_endpoint_with_cli(
    plan: StripeWebhookEndpointPlan,
    *,
    live_mode: bool = False,
    timeout: int = 30,
) -> CreatedStripeWebhookEndpoint:
    """Create a Stripe webhook endpoint through logged-in Stripe CLI auth."""
    argv = [
        "stripe",
        "webhook_endpoints",
        "create",
        "--confirm",
        "--url",
        plan.url,
    ]
    for event in plan.enabled_events:
        argv.extend(["--enabled-events", event])
    if plan.description:
        argv.extend(["--description", plan.description])
    if plan.api_version:
        argv.extend(["--api-version", plan.api_version])
    if plan.connect:
        argv.extend(["--connect", "true"])
    if live_mode:
        argv.append("--live")

    response = _run_stripe_cli_json(argv, command="webhook_endpoints create", timeout=timeout)
    endpoint_id = response.get("id")
    signing_secret = response.get("secret")
    if not isinstance(endpoint_id, str) or not endpoint_id:
        raise StripeWebhookRotatorError("Stripe CLI webhook response did not include an endpoint id.")
    if not isinstance(signing_secret, str) or not signing_secret:
        raise StripeWebhookRotatorError("Stripe CLI webhook response did not include a signing secret.")
    livemode = response.get("livemode")
    status = response.get("status")
    return CreatedStripeWebhookEndpoint(
        endpoint_id=endpoint_id,
        signing_secret=signing_secret,
        livemode=livemode if isinstance(livemode, bool) else None,
        status=status if isinstance(status, str) else None,
    )


def delete_stripe_webhook_endpoint(
    endpoint_id: str,
    *,
    api_key: str,
    timeout: int = 30,
) -> DeletedStripeWebhookEndpoint:
    """Delete a Stripe webhook endpoint by id."""
    safe_endpoint_id = urllib.parse.quote(endpoint_id, safe="")
    response = _stripe_request_json(
        "DELETE",
        f"/webhook_endpoints/{safe_endpoint_id}",
        api_key=api_key,
        fields=[],
        timeout=timeout,
    )
    response_id = response.get("id")
    deleted = response.get("deleted") is True
    return DeletedStripeWebhookEndpoint(
        endpoint_id=response_id if isinstance(response_id, str) and response_id else endpoint_id,
        deleted=deleted,
        message="" if deleted else "Stripe delete response did not confirm deletion.",
    )


def delete_stripe_webhook_endpoint_with_cli(
    endpoint_id: str,
    *,
    live_mode: bool = False,
    timeout: int = 30,
) -> DeletedStripeWebhookEndpoint:
    """Delete a Stripe webhook endpoint through logged-in Stripe CLI auth."""
    argv = ["stripe", "webhook_endpoints", "delete", endpoint_id, "--confirm"]
    if live_mode:
        argv.append("--live")

    response = _run_stripe_cli_json(argv, command="webhook_endpoints delete", timeout=timeout)
    response_id = response.get("id")
    deleted = response.get("deleted") is True
    return DeletedStripeWebhookEndpoint(
        endpoint_id=response_id if isinstance(response_id, str) and response_id else endpoint_id,
        deleted=deleted,
        message="" if deleted else "Stripe CLI delete response did not confirm deletion.",
    )


def _safe_delete_stripe_webhook_endpoint(
    endpoint_id: str,
    *,
    api_key: str,
) -> DeletedStripeWebhookEndpoint:
    try:
        return delete_stripe_webhook_endpoint(endpoint_id, api_key=api_key)
    except StripeWebhookRotatorError as exc:
        return DeletedStripeWebhookEndpoint(
            endpoint_id=endpoint_id,
            deleted=False,
            message=str(exc),
        )


def _safe_delete_stripe_webhook_endpoint_with_cli(
    endpoint_id: str,
    *,
    live_mode: bool,
) -> DeletedStripeWebhookEndpoint:
    try:
        return delete_stripe_webhook_endpoint_with_cli(endpoint_id, live_mode=live_mode)
    except StripeWebhookRotatorError as exc:
        return DeletedStripeWebhookEndpoint(
            endpoint_id=endpoint_id,
            deleted=False,
            message=str(exc),
        )


def rotate_stripe_webhook_endpoint(
    config: SyncConfig,
    secret_name: str,
    *,
    source_secret_name: str,
    url: str,
    enabled_events: tuple[str, ...] | list[str],
    description: str | None = None,
    api_version: str | None = None,
    connect: bool = False,
    delete_previous_endpoint_id: str | None = None,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
    use_stripe_cli_auth: bool = False,
    stripe_cli_live_mode: bool = False,
) -> StripeWebhookRotationResult:
    """Create a Stripe webhook endpoint and propagate its signing secret."""
    plan = build_stripe_webhook_endpoint_plan(
        config,
        secret_name,
        source_secret_name=source_secret_name,
        url=url,
        enabled_events=enabled_events,
        description=description,
        api_version=api_version,
        connect=connect,
    )
    api_key = None
    if use_stripe_cli_auth:
        key_source = f"stripe-cli:{'live' if stripe_cli_live_mode else 'test'}"
        created = create_stripe_webhook_endpoint_with_cli(
            plan,
            live_mode=stripe_cli_live_mode,
        )
    else:
        api_key, key_source = resolve_stripe_api_key(config, plan.source_secret_name)
        created = create_stripe_webhook_endpoint(plan, api_key=api_key)

    def delete_created(endpoint_id: str) -> DeletedStripeWebhookEndpoint:
        if use_stripe_cli_auth:
            return _safe_delete_stripe_webhook_endpoint_with_cli(
                endpoint_id,
                live_mode=stripe_cli_live_mode,
            )
        if api_key is None:
            return DeletedStripeWebhookEndpoint(
                endpoint_id=endpoint_id,
                deleted=False,
                message="Stripe API key was not resolved.",
            )
        return _safe_delete_stripe_webhook_endpoint(endpoint_id, api_key=api_key)

    try:
        propagation = propagate_secret(
            config,
            secret_name,
            created.signing_secret,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
            allow_manual_cutover=True,
        )
    except Exception as exc:
        cleanup = delete_created(created.endpoint_id)
        error = f"Propagation raised {type(exc).__name__} after creating Stripe webhook endpoint."
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return StripeWebhookRotationResult(
            plan=plan,
            stripe_key_source=key_source,
            created=created,
            propagation=None,
            cleanup_of_created_endpoint=cleanup,
            error=error,
        )
    if not propagation.ok:
        cleanup = delete_created(created.endpoint_id)
        error = "Propagation failed after creating Stripe webhook endpoint."
        if not cleanup.deleted:
            error += f" Cleanup failed: {cleanup.message}"
        return StripeWebhookRotationResult(
            plan=plan,
            stripe_key_source=key_source,
            created=created,
            propagation=propagation,
            cleanup_of_created_endpoint=cleanup,
            error=error,
        )

    deleted_previous = None
    error = None
    if delete_previous_endpoint_id:
        deleted_previous = delete_created(delete_previous_endpoint_id)
        if not deleted_previous.deleted:
            error = f"Previous Stripe webhook endpoint delete failed: {deleted_previous.message}"

    return StripeWebhookRotationResult(
        plan=plan,
        stripe_key_source=key_source,
        created=created,
        propagation=propagation,
        deleted_previous_endpoint=deleted_previous,
        error=error,
    )

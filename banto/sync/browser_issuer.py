# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Browser-assisted credential issuance for provider console flows.

This module intentionally keeps issued secret values inside the local banto
process. Recipes describe where to click and where the one-time credential is
shown, but command output only returns metadata.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import SyncConfig
from .propagation import (
    PropagationPlan,
    PropagationResult,
    build_propagation_plan,
    propagate_secret,
    validate_propagation_plan,
)

SUPPORTED_RECIPE_VERSION = 1
SUPPORTED_ACTIONS = {
    "click",
    "fill",
    "press",
    "select",
    "wait_for_selector",
    "wait_for_url",
    "wait_for_timeout",
    "human_checkpoint",
}
SUPPORTED_CAPTURE_SOURCES = {"text", "input", "attribute"}
_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class BrowserIssuerError(RuntimeError):
    """Raised when browser-assisted issuance cannot proceed safely."""


@dataclass(frozen=True)
class BrowserRecipeStep:
    """One browser action in a credential issuance recipe."""

    action: str
    selector: str | None = None
    text: str | None = None
    key: str | None = None
    value: str | None = None
    url: str | None = None
    state: str | None = None
    timeout_ms: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class BrowserSecretCapture:
    """Instruction for capturing a newly issued secret from the page."""

    selector: str
    source: str = "text"
    attribute: str | None = None
    regex: str | None = None
    min_length: int = 8


@dataclass(frozen=True)
class BrowserIssuerRecipe:
    """Validated browser issuance recipe."""

    name: str
    provider: str
    start_url: str
    steps: tuple[BrowserRecipeStep, ...]
    capture: BrowserSecretCapture
    metadata_selectors: dict[str, str] = field(default_factory=dict)
    nav_timeout_ms: int = 30_000
    action_timeout_ms: int = 15_000


@dataclass(frozen=True)
class BrowserCaptureResult:
    """Sensitive capture result from the browser runner."""

    secret_value: str = field(repr=False)
    metadata: dict[str, str]


@dataclass(frozen=True)
class BrowserIssuePlan:
    """Dry-run safe issuance plan."""

    propagation_plan: PropagationPlan
    recipe: BrowserIssuerRecipe
    profile_dir: Path
    headless: bool


@dataclass(frozen=True)
class BrowserIssueResult:
    """End-to-end browser issuance result without secret values."""

    plan: BrowserIssuePlan
    propagation: PropagationResult | None
    metadata: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.propagation is not None and self.propagation.ok


def load_browser_issuer_recipe(path: Path | str) -> BrowserIssuerRecipe:
    """Load and validate a browser issuance recipe from JSON."""
    recipe_path = Path(path)
    try:
        raw = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserIssuerError(f"Failed to load browser recipe: {exc}") from exc
    if not isinstance(raw, dict):
        raise BrowserIssuerError("Browser recipe must be a JSON object.")
    return browser_recipe_from_dict(raw)


def browser_recipe_from_dict(raw: dict[str, Any]) -> BrowserIssuerRecipe:
    """Validate a recipe dictionary and return a typed recipe."""
    version = raw.get("version")
    if version != SUPPORTED_RECIPE_VERSION:
        raise BrowserIssuerError(
            f"Unsupported browser recipe version: {version!r}. "
            f"Expected {SUPPORTED_RECIPE_VERSION}."
        )

    name = _required_str(raw, "name")
    provider = _required_str(raw, "provider").lower()
    start_url = _required_str(raw, "start_url")
    _validate_start_url(start_url)

    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise BrowserIssuerError("Browser recipe requires a non-empty steps array.")
    steps = tuple(_parse_step(item, index) for index, item in enumerate(steps_raw, start=1))

    capture_raw = raw.get("capture")
    if not isinstance(capture_raw, dict):
        raise BrowserIssuerError("Browser recipe requires a capture object.")
    capture = _parse_capture(capture_raw)

    metadata_raw = raw.get("metadata_selectors", {})
    if not isinstance(metadata_raw, dict):
        raise BrowserIssuerError("metadata_selectors must be an object when present.")
    metadata_selectors = {
        str(key): str(value)
        for key, value in metadata_raw.items()
        if str(key).strip() and str(value).strip()
    }

    return BrowserIssuerRecipe(
        name=name,
        provider=provider,
        start_url=start_url,
        steps=steps,
        capture=capture,
        metadata_selectors=metadata_selectors,
        nav_timeout_ms=_bounded_timeout(raw.get("nav_timeout_ms"), 30_000),
        action_timeout_ms=_bounded_timeout(raw.get("action_timeout_ms"), 15_000),
    )


def default_browser_profile_dir(recipe: BrowserIssuerRecipe) -> Path:
    """Return a stable local browser profile path for provider login state."""
    slug = _SAFE_SLUG_RE.sub("-", recipe.provider).strip("-") or "provider"
    return Path.home() / ".local" / "state" / "banto" / "browser-profiles" / slug


def build_browser_issue_plan(
    config: SyncConfig,
    secret_name: str,
    recipe: BrowserIssuerRecipe,
    *,
    profile_dir: Path | str | None = None,
    headless: bool = False,
) -> BrowserIssuePlan:
    """Validate that a configured secret can use a browser issuance recipe."""
    plan = build_propagation_plan(config, secret_name)
    validate_propagation_plan(plan)
    if recipe.provider not in {"*", plan.provider}:
        raise BrowserIssuerError(
            f"Recipe provider '{recipe.provider}' does not match secret provider "
            f"'{plan.provider}'."
        )
    resolved_profile = Path(profile_dir) if profile_dir else default_browser_profile_dir(recipe)
    return BrowserIssuePlan(
        propagation_plan=plan,
        recipe=recipe,
        profile_dir=resolved_profile,
        headless=headless,
    )


def issue_secret_with_browser(
    config: SyncConfig,
    secret_name: str,
    recipe: BrowserIssuerRecipe,
    *,
    profile_dir: Path | str | None = None,
    headless: bool = False,
    do_validate: bool = False,
    smoke_command: str | None = None,
    smoke_preset: str | None = None,
) -> BrowserIssueResult:
    """Run a browser recipe, capture the new secret, and propagate it safely."""
    plan = build_browser_issue_plan(
        config,
        secret_name,
        recipe,
        profile_dir=profile_dir,
        headless=headless,
    )
    try:
        captured = _run_playwright_recipe(plan)
        propagation = propagate_secret(
            config,
            secret_name,
            captured.secret_value,
            do_validate=do_validate,
            smoke_command=smoke_command,
            smoke_preset=smoke_preset,
        )
        error = None if propagation.ok else "Propagation failed after browser issuance."
        return BrowserIssueResult(
            plan=plan,
            propagation=propagation,
            metadata=captured.metadata,
            error=error,
        )
    except BrowserIssuerError as exc:
        return BrowserIssueResult(
            plan=plan,
            propagation=None,
            metadata={},
            error=str(exc),
        )
    except Exception as exc:
        return BrowserIssueResult(
            plan=plan,
            propagation=None,
            metadata={},
            error=f"Browser-issued secret propagation failed: {type(exc).__name__}",
        )


def _run_playwright_recipe(plan: BrowserIssuePlan) -> BrowserCaptureResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserIssuerError(
            "Playwright is required for browser issuance. Install with "
            "`python -m pip install 'banto[browser]'` and run "
            "`python -m playwright install chromium`."
        ) from exc

    recipe = plan.recipe
    plan.profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(plan.profile_dir),
                headless=plan.headless,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    recipe.start_url,
                    wait_until="domcontentloaded",
                    timeout=recipe.nav_timeout_ms,
                )
                context_data = _template_context(plan)
                for step in recipe.steps:
                    _execute_step(page, step, recipe, context_data)
                secret_value = _capture_secret(page, recipe.capture)
                metadata = _capture_metadata(page, recipe.metadata_selectors)
                return BrowserCaptureResult(
                    secret_value=secret_value,
                    metadata=metadata,
                )
            finally:
                context.close()
    except PlaywrightTimeoutError as exc:
        raise BrowserIssuerError("Browser issuance timed out.") from exc
    except BrowserIssuerError:
        raise
    except Exception as exc:
        raise BrowserIssuerError(f"Browser issuance failed: {type(exc).__name__}") from exc


def _execute_step(page: Any, step: BrowserRecipeStep, recipe: BrowserIssuerRecipe,
                  context_data: dict[str, str]) -> None:
    timeout = step.timeout_ms or recipe.action_timeout_ms
    if step.action == "click":
        page.locator(_require_selector(step)).click(timeout=timeout)
        return
    if step.action == "fill":
        if step.text is None:
            raise BrowserIssuerError("fill step requires text.")
        page.locator(_require_selector(step)).fill(
            _render_template(step.text, context_data),
            timeout=timeout,
        )
        return
    if step.action == "press":
        if not step.key:
            raise BrowserIssuerError("press step requires key.")
        if step.selector:
            page.locator(step.selector).press(step.key, timeout=timeout)
        else:
            page.keyboard.press(step.key)
        return
    if step.action == "select":
        if step.value is None:
            raise BrowserIssuerError("select step requires value.")
        page.locator(_require_selector(step)).select_option(step.value, timeout=timeout)
        return
    if step.action == "wait_for_selector":
        page.wait_for_selector(
            _require_selector(step),
            state=step.state or "visible",
            timeout=timeout,
        )
        return
    if step.action == "wait_for_url":
        if not step.url:
            raise BrowserIssuerError("wait_for_url step requires url.")
        page.wait_for_url(step.url, timeout=timeout)
        return
    if step.action == "wait_for_timeout":
        page.wait_for_timeout(timeout)
        return
    if step.action == "human_checkpoint":
        message = step.message or "Complete the browser checkpoint, then press Enter."
        input(f"{message} ")
        return
    raise BrowserIssuerError(f"Unsupported browser action: {step.action}")


def _capture_secret(page: Any, capture: BrowserSecretCapture) -> str:
    locator = page.locator(capture.selector)
    if capture.source == "input":
        raw_value = locator.input_value(timeout=15_000)
    elif capture.source == "attribute":
        if not capture.attribute:
            raise BrowserIssuerError("attribute capture requires attribute.")
        raw_value = locator.get_attribute(capture.attribute, timeout=15_000)
    else:
        raw_value = locator.text_content(timeout=15_000)

    if raw_value is None:
        raise BrowserIssuerError("Secret capture returned no value.")
    value = raw_value.strip()
    if capture.regex:
        match = re.search(capture.regex, value)
        if not match:
            raise BrowserIssuerError("Secret capture regex did not match.")
        value = (match.group(1) if match.groups() else match.group(0)).strip()

    _validate_captured_secret(value, capture.min_length)
    return value


def _capture_metadata(page: Any, selectors: dict[str, str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, selector in selectors.items():
        try:
            text = page.locator(selector).text_content(timeout=5_000)
        except Exception:
            continue
        if text is not None and text.strip():
            metadata[key] = _redact_metadata_if_secret_like(text.strip())
    return metadata


def _validate_captured_secret(value: str, min_length: int) -> None:
    if len(value) < min_length:
        raise BrowserIssuerError("Captured secret is shorter than the recipe minimum.")
    if "\n" in value or "\r" in value:
        raise BrowserIssuerError("Captured secret contains a newline.")
    if "..." in value or "•••" in value or "***" in value:
        raise BrowserIssuerError("Captured value looks redacted; refusing to store it.")


def _redact_metadata_if_secret_like(value: str) -> str:
    if _looks_like_raw_secret(value):
        return "[redacted]"
    return value


def _looks_like_raw_secret(value: str) -> bool:
    compact = value.strip()
    if any(prefix and compact.startswith(prefix) for prefix in (
        "sk-",
        "ghp_",
        "github_pat_",
        "xai-",
        "AIza",
        "rk_live_",
        "rk_test_",
        "SG.",
    )):
        return True
    if len(compact) >= 32 and not re.search(r"\s", compact):
        classes = sum(
            bool(re.search(pattern, compact))
            for pattern in (r"[a-z]", r"[A-Z]", r"\d")
        )
        return classes >= 2 and bool(re.search(r"[-_=./+]", compact))
    return False


def _template_context(plan: BrowserIssuePlan) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {
        "secret_name": plan.propagation_plan.secret_name,
        "env_name": plan.propagation_plan.env_name,
        "provider": plan.propagation_plan.provider,
        "timestamp": now.strftime("%Y%m%dt%H%M%SZ").lower(),
    }


def _render_template(value: str, context_data: dict[str, str]) -> str:
    rendered = value
    for key, replacement in context_data.items():
        rendered = rendered.replace("{{" + key + "}}", replacement)
    return rendered


def _parse_step(raw: Any, index: int) -> BrowserRecipeStep:
    if not isinstance(raw, dict):
        raise BrowserIssuerError(f"Step {index} must be an object.")
    action = str(raw.get("action", "")).strip()
    if action not in SUPPORTED_ACTIONS:
        raise BrowserIssuerError(f"Step {index} has unsupported action: {action!r}.")
    return BrowserRecipeStep(
        action=action,
        selector=_optional_str(raw.get("selector")),
        text=_optional_str(raw.get("text")),
        key=_optional_str(raw.get("key")),
        value=_optional_str(raw.get("value")),
        url=_optional_str(raw.get("url")),
        state=_optional_str(raw.get("state")),
        timeout_ms=_bounded_timeout(raw.get("timeout_ms"), None),
        message=_optional_str(raw.get("message")),
    )


def _parse_capture(raw: dict[str, Any]) -> BrowserSecretCapture:
    selector = _required_str(raw, "selector")
    source = str(raw.get("source", "text")).strip()
    if source not in SUPPORTED_CAPTURE_SOURCES:
        raise BrowserIssuerError(f"Unsupported capture source: {source!r}.")
    min_length = raw.get("min_length", 8)
    if not isinstance(min_length, int) or min_length <= 0:
        raise BrowserIssuerError("capture.min_length must be a positive integer.")
    return BrowserSecretCapture(
        selector=selector,
        source=source,
        attribute=_optional_str(raw.get("attribute")),
        regex=_optional_str(raw.get("regex")),
        min_length=min_length,
    )


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BrowserIssuerError(f"Browser recipe requires non-empty {key}.")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    stripped = value.strip()
    return stripped or None


def _bounded_timeout(raw_value: Any, default: int | None) -> int | None:
    if raw_value is None:
        return default
    if not isinstance(raw_value, int):
        raise BrowserIssuerError("Timeout values must be integers.")
    if raw_value <= 0 or raw_value > 600_000:
        raise BrowserIssuerError("Timeout values must be between 1 and 600000 ms.")
    return raw_value


def _validate_start_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise BrowserIssuerError("start_url must be https, or localhost http for testing.")


def _require_selector(step: BrowserRecipeStep) -> str:
    if not step.selector:
        raise BrowserIssuerError(f"{step.action} step requires selector.")
    return step.selector

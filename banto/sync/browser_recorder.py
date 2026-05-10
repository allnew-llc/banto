# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Record provider-console browser actions into safe banto recipes.

The recorder stores selectors and non-sensitive form values only. It never
writes captured API keys to recipe files, scripts, JSON output, or manifests.
If the authoring run creates a temporary exposed key, the recorder can capture
its provider key id/label as metadata so a later browser-issue run can revoke it
after a replacement has propagated.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .browser_issuer import (
    BrowserIssuerError,
    SUPPORTED_CAPTURE_SOURCES,
    _redact_metadata_if_secret_like,
    _validate_start_url,
    _validate_retirement_identifier,
)
from .capabilities import classify_secret
from .config import SyncConfig
from .propagation import build_propagation_plan


@dataclass(frozen=True)
class BrowserRecordingPlan:
    """Dry-run safe plan for recording a browser issuance recipe."""

    secret_name: str
    provider: str
    start_url: str
    output_path: Path
    profile_dir: Path
    headless: bool = False
    capture_selector: str | None = None
    capture_source: str = "text"
    capture_attribute: str | None = None
    capture_regex: str | None = None
    capture_min_length: int = 8
    capture_from_last_click: bool = False
    metadata_selectors: dict[str, str] = field(default_factory=dict)
    script_out: Path | None = None
    exposed_key_id_selector: str | None = None
    exposed_key_label_selector: str | None = None
    exposed_key_id: str | None = None
    exposed_key_label: str | None = None
    exposure_manifest_out: Path | None = None
    revoke_recipe_path: Path | None = None


@dataclass(frozen=True)
class BrowserRecordingResult:
    """Result of browser recipe recording without secret values."""

    recipe_path: Path
    script_path: Path | None
    exposure_manifest_path: Path | None
    provider: str
    start_url: str
    action_count: int
    capture_selector: str
    metadata_keys: tuple[str, ...]
    exposed_key_id_recorded: bool
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def default_recording_profile_dir(provider: str) -> Path:
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in provider).strip("-")
    return Path.home() / ".local" / "state" / "banto" / "browser-profiles" / (slug or "provider")


def default_recipe_output(secret_name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in secret_name).strip("-")
    return Path("recipes") / f"{safe or 'secret'}-browser-issue.json"


def build_browser_recording_plan(
    config: SyncConfig,
    secret_name: str,
    *,
    start_url: str,
    provider: str | None = None,
    output_path: Path | str | None = None,
    profile_dir: Path | str | None = None,
    headless: bool = False,
    capture_selector: str | None = None,
    capture_source: str = "text",
    capture_attribute: str | None = None,
    capture_regex: str | None = None,
    capture_min_length: int = 8,
    capture_from_last_click: bool = False,
    metadata_selectors: dict[str, str] | None = None,
    script_out: Path | str | None = None,
    exposed_key_id_selector: str | None = None,
    exposed_key_label_selector: str | None = None,
    exposed_key_id: str | None = None,
    exposed_key_label: str | None = None,
    exposure_manifest_out: Path | str | None = None,
    revoke_recipe_path: Path | str | None = None,
) -> BrowserRecordingPlan:
    """Build a safe browser-record plan from config plus CLI options."""
    if not start_url.strip():
        raise BrowserIssuerError("browser-record requires --start-url.")
    _validate_start_url(start_url.strip())
    if capture_source not in SUPPORTED_CAPTURE_SOURCES:
        raise BrowserIssuerError(f"Unsupported capture source: {capture_source!r}.")

    resolved_provider = provider.strip().lower() if provider and provider.strip() else ""
    if not resolved_provider:
        entry = config.get_secret(secret_name)
        if entry is None:
            raise BrowserIssuerError("browser-record requires --provider for unknown secrets.")
        resolved_provider = classify_secret(secret_name, entry.env_name).provider
    if not resolved_provider:
        raise BrowserIssuerError("browser-record could not resolve provider.")

    if exposed_key_id:
        _validate_retirement_identifier(exposed_key_id)

    # This validates the configured secret when present, but recording also
    # supports draft recipes before a secret is fully wired into sync.json.
    if config.get_secret(secret_name) is not None:
        build_propagation_plan(config, secret_name)

    out = Path(output_path) if output_path else default_recipe_output(secret_name)
    profile = Path(profile_dir) if profile_dir else default_recording_profile_dir(resolved_provider)
    return BrowserRecordingPlan(
        secret_name=secret_name,
        provider=resolved_provider,
        start_url=start_url.strip(),
        output_path=out,
        profile_dir=profile,
        headless=headless,
        capture_selector=capture_selector.strip() if capture_selector else None,
        capture_source=capture_source,
        capture_attribute=capture_attribute,
        capture_regex=capture_regex,
        capture_min_length=capture_min_length,
        capture_from_last_click=capture_from_last_click,
        metadata_selectors=metadata_selectors or {},
        script_out=Path(script_out) if script_out else None,
        exposed_key_id_selector=exposed_key_id_selector,
        exposed_key_label_selector=exposed_key_label_selector,
        exposed_key_id=exposed_key_id,
        exposed_key_label=exposed_key_label,
        exposure_manifest_out=Path(exposure_manifest_out) if exposure_manifest_out else None,
        revoke_recipe_path=Path(revoke_recipe_path) if revoke_recipe_path else None,
    )


def recipe_dict_from_recorded_actions(
    plan: BrowserRecordingPlan,
    actions: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Convert raw browser events into a banto browser-issue recipe."""
    steps, warnings = _steps_from_recorded_actions(actions)
    capture_selector = plan.capture_selector
    if not capture_selector and plan.capture_from_last_click:
        capture_selector = _last_click_selector(actions)
    if not capture_selector:
        raise BrowserIssuerError(
            "Recording needs --capture-selector or --capture-from-last-click."
        )

    capture: dict[str, Any] = {
        "selector": capture_selector,
        "source": plan.capture_source,
        "min_length": plan.capture_min_length,
    }
    if plan.capture_attribute:
        capture["attribute"] = plan.capture_attribute
    if plan.capture_regex:
        capture["regex"] = plan.capture_regex

    recipe = {
        "version": 1,
        "name": f"{plan.secret_name}-browser-issue",
        "provider": plan.provider,
        "start_url": plan.start_url,
        "steps": steps,
        "capture": capture,
    }
    if plan.metadata_selectors:
        recipe["metadata_selectors"] = dict(plan.metadata_selectors)
    return recipe, warnings


def record_browser_recipe(plan: BrowserRecordingPlan) -> BrowserRecordingResult:
    """Launch a browser, record actions, and write recipe/script artifacts."""
    try:
        actions, exposed = _run_playwright_recorder(plan)
        recipe, warnings = recipe_dict_from_recorded_actions(plan, actions)
        _write_json(plan.output_path, recipe)
        script_path = _write_script(plan)
        manifest_path = _write_exposure_manifest(plan, exposed)
        return BrowserRecordingResult(
            recipe_path=plan.output_path,
            script_path=script_path,
            exposure_manifest_path=manifest_path,
            provider=plan.provider,
            start_url=plan.start_url,
            action_count=len(recipe["steps"]),
            capture_selector=recipe["capture"]["selector"],
            metadata_keys=tuple(sorted(plan.metadata_selectors)),
            exposed_key_id_recorded=bool(exposed.get("key_id")),
            warnings=warnings,
        )
    except BrowserIssuerError as exc:
        return BrowserRecordingResult(
            recipe_path=plan.output_path,
            script_path=plan.script_out,
            exposure_manifest_path=plan.exposure_manifest_out,
            provider=plan.provider,
            start_url=plan.start_url,
            action_count=0,
            capture_selector=plan.capture_selector or "",
            metadata_keys=tuple(sorted(plan.metadata_selectors)),
            exposed_key_id_recorded=False,
            error=str(exc),
        )


def _run_playwright_recorder(plan: BrowserRecordingPlan) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserIssuerError(
            "Playwright is required for browser recording. Install with "
            "`python -m pip install 'banto[browser]'` and run "
            "`python -m playwright install chromium`."
        ) from exc

    plan.profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(plan.profile_dir),
            headless=plan.headless,
        )
        try:
            context.add_init_script(_RECORDER_INIT_SCRIPT)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(plan.start_url, wait_until="domcontentloaded", timeout=30_000)
            input("Record the provider flow in the opened browser, then press Enter here. ")
            actions = page.evaluate("window.__bantoRecordedActions || []")
            exposed = _capture_exposure_metadata(page, plan)
            return actions if isinstance(actions, list) else [], exposed
        finally:
            context.close()


def _steps_from_recorded_actions(actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    sensitive_checkpoint_selectors: set[str] = set()
    for action in actions:
        kind = str(action.get("action", ""))
        selector = str(action.get("selector", "")).strip()
        if kind == "click" and selector:
            steps.append({"action": "click", "selector": selector})
        elif kind == "fill" and selector:
            text = str(action.get("text", ""))
            if action.get("sensitive") is True:
                if selector not in sensitive_checkpoint_selectors:
                    steps.append({
                        "action": "human_checkpoint",
                        "message": f"Complete sensitive input for {selector}, then press Enter.",
                    })
                    sensitive_checkpoint_selectors.add(selector)
                    warnings.append(f"sensitive fill replaced with human checkpoint: {selector}")
            else:
                steps.append({"action": "fill", "selector": selector, "text": text})
        elif kind == "select" and selector:
            steps.append({"action": "select", "selector": selector, "value": str(action.get("value", ""))})
        elif kind == "press":
            key = str(action.get("key", "")).strip()
            if key:
                step: dict[str, Any] = {"action": "press", "key": key}
                if selector:
                    step["selector"] = selector
                steps.append(step)

    return _dedupe_steps(steps), tuple(warnings)


def _dedupe_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for step in steps:
        if (
            deduped
            and step["action"] in {"fill", "select"}
            and deduped[-1].get("action") == step["action"]
            and deduped[-1].get("selector") == step.get("selector")
        ):
            deduped[-1] = step
        else:
            deduped.append(step)
    return deduped


def _last_click_selector(actions: list[dict[str, Any]]) -> str | None:
    for action in reversed(actions):
        if action.get("action") == "click" and str(action.get("selector", "")).strip():
            return str(action["selector"]).strip()
    return None


def _capture_exposure_metadata(page: Any, plan: BrowserRecordingPlan) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if plan.exposed_key_id:
        metadata["key_id"] = plan.exposed_key_id
    elif plan.exposed_key_id_selector:
        value = _safe_text_content(page, plan.exposed_key_id_selector)
        if value:
            _validate_retirement_identifier(value)
            metadata["key_id"] = value
    if plan.exposed_key_label:
        metadata["key_label"] = plan.exposed_key_label
    elif plan.exposed_key_label_selector:
        value = _safe_text_content(page, plan.exposed_key_label_selector)
        if value:
            metadata["key_label"] = value
    return metadata


def _safe_text_content(page: Any, selector: str) -> str | None:
    text = page.locator(selector).text_content(timeout=5_000)
    if text is None:
        return None
    cleaned = _redact_metadata_if_secret_like(text.strip())
    if cleaned == "[redacted]":
        raise BrowserIssuerError(
            f"Exposure metadata selector {selector!r} appears to point at a secret value."
        )
    return cleaned or None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_script(plan: BrowserRecordingPlan) -> Path | None:
    if plan.script_out is None:
        return None
    plan.script_out.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "banto",
        "sync",
        "browser-issue",
        plan.secret_name,
        "--recipe",
        str(plan.output_path),
        "--profile-dir",
        str(plan.profile_dir),
    ]
    if plan.exposure_manifest_out is not None:
        parts.extend(["--exposure-manifest", str(plan.exposure_manifest_out)])
    content = "#!/bin/sh\nset -eu\n" + " ".join(shlex.quote(part) for part in parts) + ' "$@"\n'
    plan.script_out.write_text(content, encoding="utf-8")
    os.chmod(plan.script_out, 0o700)
    return plan.script_out


def _write_exposure_manifest(plan: BrowserRecordingPlan, metadata: dict[str, str]) -> Path | None:
    if plan.exposure_manifest_out is None:
        return None
    if "key_id" not in metadata:
        raise BrowserIssuerError(
            "Exposure manifest requested, but no exposed key id was captured."
        )
    payload: dict[str, Any] = {
        "version": 1,
        "secret_name": plan.secret_name,
        "provider": plan.provider,
        "key_id": metadata["key_id"],
        "recipe": str(plan.output_path),
    }
    if metadata.get("key_label"):
        payload["key_label"] = metadata["key_label"]
    if plan.revoke_recipe_path is not None:
        payload["revoke_recipe"] = str(plan.revoke_recipe_path)
    _write_json(plan.exposure_manifest_out, payload)
    return plan.exposure_manifest_out


_RECORDER_INIT_SCRIPT = r"""
(() => {
  if (window.__bantoRecorderInstalled) return;
  window.__bantoRecorderInstalled = true;
  window.__bantoRecordedActions = [];
  const sensitiveWords = /(secret|token|api[-_ ]?key|password|passwd|otp|mfa|code|credential)/i;
  const cssEscape = window.CSS && window.CSS.escape ? window.CSS.escape : (value) => String(value).replace(/"/g, '\\"');
  function attrSelector(name, value) {
    return '[' + name + '="' + cssEscape(value) + '"]';
  }
  function selectorFor(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return "";
    const el = element.closest('button,a,input,textarea,select,[role="button"],[data-testid],[data-test],[aria-label]') || element;
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute("data-testid")) return attrSelector("data-testid", el.getAttribute("data-testid"));
    if (el.getAttribute("data-test")) return attrSelector("data-test", el.getAttribute("data-test"));
    if (el.id) return "#" + cssEscape(el.id);
    if (el.getAttribute("name")) return tag + attrSelector("name", el.getAttribute("name"));
    if (el.getAttribute("aria-label")) return tag + attrSelector("aria-label", el.getAttribute("aria-label"));
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && current.tagName.toLowerCase() !== "html") {
      const currentTag = current.tagName.toLowerCase();
      let index = 1;
      let sibling = current;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName.toLowerCase() === currentTag) index += 1;
      }
      parts.unshift(currentTag + ":nth-of-type(" + index + ")");
      current = current.parentElement;
      if (parts.length >= 5) break;
    }
    return parts.join(" > ");
  }
  function isSensitiveInput(el) {
    const tag = el.tagName ? el.tagName.toLowerCase() : "";
    if (!["input", "textarea"].includes(tag)) return false;
    const type = String(el.getAttribute("type") || "").toLowerCase();
    const hints = [
      el.getAttribute("name"),
      el.getAttribute("id"),
      el.getAttribute("autocomplete"),
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder")
    ].filter(Boolean).join(" ");
    return ["password", "hidden"].includes(type) || sensitiveWords.test(hints);
  }
  document.addEventListener("click", (event) => {
    const selector = selectorFor(event.target);
    if (selector) window.__bantoRecordedActions.push({action: "click", selector});
  }, true);
  document.addEventListener("input", (event) => {
    const el = event.target;
    if (!el || !["INPUT", "TEXTAREA"].includes(el.tagName)) return;
    const selector = selectorFor(el);
    if (!selector) return;
    if (isSensitiveInput(el)) {
      window.__bantoRecordedActions.push({action: "fill", selector, sensitive: true});
      return;
    }
    window.__bantoRecordedActions.push({action: "fill", selector, text: String(el.value || "")});
  }, true);
  document.addEventListener("change", (event) => {
    const el = event.target;
    if (!el || el.tagName !== "SELECT") return;
    const selector = selectorFor(el);
    if (selector) window.__bantoRecordedActions.push({action: "select", selector, value: String(el.value || "")});
  }, true);
  document.addEventListener("keydown", (event) => {
    if (!["Enter", "Tab", "Escape"].includes(event.key)) return;
    const selector = selectorFor(event.target);
    window.__bantoRecordedActions.push({action: "press", selector, key: event.key});
  }, true);
})();
"""

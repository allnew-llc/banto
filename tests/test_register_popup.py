"""Tests for register_popup — XSS prevention and HTML safety."""

import pytest

from banto.register_popup import (
    ASC_REGISTER_ITEMS,
    _build_asc_html,
    _build_html,
    _normalize_register_item,
    _safe_attr,
)


# Codex-recommended XSS regression payloads
XSS_PAYLOADS = [
    "foo');alert(1)//",
    "</script><script>window.pwned=1</script>",
    "a\\b",
    "line1\nline2",
]


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_hint_is_html_escaped_in_data_attribute(payload: str) -> None:
    """HINT must appear only in data-hint attr, never as inline JS literal."""
    html = _build_html(provider_hint=payload)
    escaped = _safe_attr(payload)

    assert f'data-hint="{escaped}"' in html
    assert "</script><script>" not in html


def test_hint_read_from_dataset_not_inline_js() -> None:
    """HINT must be read via dataset.hint, not embedded in a JS literal."""
    html = _build_html(provider_hint="test")

    assert "dataset.hint" in html
    assert 'const HINT = "test"' not in html


def test_safe_attr_escapes_dangerous_chars() -> None:
    assert _safe_attr('<script>') == "&lt;script&gt;"
    assert _safe_attr('"quoted"') == "&quot;quoted&quot;"
    assert _safe_attr("it's") == "it&#x27;s"
    assert _safe_attr("a&b") == "a&amp;b"


def test_provider_presets_populate_dropdown() -> None:
    """All PROVIDER_PRESETS keys must appear as <option> values."""
    from banto.register_popup import PROVIDER_PRESETS

    html = _build_html()
    for provider in PROVIDER_PRESETS:
        assert f'value="{provider}"' in html, f"Missing dropdown option: {provider}"


def test_provider_guides_render_provider_specific_help() -> None:
    html = _build_html(provider_hint="xai")

    assert "xAI management key" in html
    assert "banto sync xai-api-key xai --team-id" in html
    assert "xai-management|XAI_MANAGEMENT_API_KEY" in html
    assert "Open issuer console" in html


def test_batch_registration_ui_and_endpoint_are_present() -> None:
    html = _build_html()

    assert 'id="batch-mode"' in html
    assert 'id="batch-entries"' in html
    assert "/register-many" in html
    assert "provider|ENV_NAME=value" in html


def test_normalize_register_item_validates_without_leaking_value() -> None:
    item = _normalize_register_item({
        "provider": "XAI-Management",
        "env_name": "XAI_MANAGEMENT_API_KEY",
        "value": "secret-value",
        "description": "desc",
    })

    assert item["provider"] == "xai-management"
    assert item["env_name"] == "XAI_MANAGEMENT_API_KEY"
    assert item["value"] == "secret-value"


def test_normalize_register_item_rejects_invalid_provider() -> None:
    with pytest.raises(ValueError, match="Invalid provider name"):
        _normalize_register_item({"provider": "../bad", "value": "secret"})


def test_custom_hint_sets_custom_field() -> None:
    """A hint not in presets should trigger the Custom path in JS."""
    html = _build_html(provider_hint="hmac_secret")

    assert 'data-hint="hmac_secret"' in html


def test_asc_registration_ui_posts_expected_providers() -> None:
    html = _build_asc_html()

    assert "Store ASC Credentials" in html
    assert "/register-many" in html
    assert 'id="issuer-id"' in html
    assert 'id="key-name"' in html
    assert 'id="key-id"' in html
    assert 'id="p8-path"' in html
    for provider, env_name, label, _description in ASC_REGISTER_ITEMS:
        assert f'provider: "{provider}"' in html
        assert f'env_name: "{env_name}"' in html
        assert label in html

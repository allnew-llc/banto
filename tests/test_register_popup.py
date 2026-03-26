"""Tests for register_popup — XSS prevention and HTML safety."""

import pytest

from banto.register_popup import _build_html, _safe_attr


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


def test_custom_hint_sets_custom_field() -> None:
    """A hint not in presets should trigger the Custom path in JS."""
    html = _build_html(provider_hint="hmac_secret")

    assert 'data-hint="hmac_secret"' in html

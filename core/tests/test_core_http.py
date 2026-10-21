"""Recorded transport fixtures only; never contact a provider."""
import json
from unittest.mock import patch

import pytest
from banto_core import http
from banto_core.broker_client import BrokerError


class Response:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.raw[:limit]


def test_fixed_transport_redacts_decoded_values_and_keys():
    dummy = "fixture-value-123"
    raw = b'{"fixture-value-123":"fixture-value-123", "escaped":"fixture-value-12\\u0033"}'
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return Response(raw)

    with patch.object(http.urllib.request, "build_opener", return_value=Opener()) as build:
        result = http.request_fixed("openai", {"model": "fixture", "store": True}, dummy)
    assert result == {"[REDACTED]": "[REDACTED]", "escaped": "[REDACTED]"}
    request, timeout = calls[0]
    assert request.full_url == http.ENDPOINTS["openai"]
    assert json.loads(request.data)["store"] is False
    assert timeout == 120
    assert build.call_args.args[0].proxies == {}
    assert isinstance(build.call_args.args[1], http.NoRedirect)


def test_unsupported_provider_fails_before_transport():
    with patch.object(http.urllib.request, "build_opener") as build:
        with pytest.raises(BrokerError, match="unsupported_request"):
            http.request_fixed("https://fixture.invalid/", {}, "dummy")
        build.assert_not_called()


def test_response_size_limit():
    class Opener:
        def open(self, request, timeout):
            return Response(b"x" * (http.MAX_MESSAGE + 1))

    with patch.object(http.urllib.request, "build_opener", return_value=Opener()):
        with pytest.raises(BrokerError, match="upstream_response_too_large"):
            http.request_fixed("anthropic", {}, "dummy")


def test_redirect_does_not_forward_credentials():
    assert http.NoRedirect().redirect_request(None, None, 302, "", {}, "https://fixture.invalid") is None

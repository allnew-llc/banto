# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Fixed provider HTTP transport. Used inside a trusted broker process only."""
import json
import urllib.request

from .broker_client import BrokerError, MAX_MESSAGE
from .redaction import redact

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_fixed(provider, payload, key):
    """Return decoded, redacted output; no caller-supplied URL or auth headers."""
    if provider not in ENDPOINTS or not isinstance(payload, dict):
        raise BrokerError("unsupported_request")
    headers = {"Content-Type": "application/json"}
    if provider == "openai":
        headers["Authorization"] = "Bearer " + key
        payload = {**payload, "store": False}
    else:
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        ENDPOINTS[provider], json.dumps(payload).encode(), headers, method="POST"
    )
    with opener.open(request, timeout=120) as response:
        raw = response.read(MAX_MESSAGE + 1)
    if len(raw) > MAX_MESSAGE:
        raise BrokerError("upstream_response_too_large")
    # Decode before filtering, so JSON escapes cannot defeat redaction.
    return redact(json.loads(raw), {key})

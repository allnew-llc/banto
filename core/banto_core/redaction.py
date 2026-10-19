# Copyright (c) 2025-2026 AllNew LLC. All rights reserved.
# Licensed under the banto Dual License; see LICENSE.
"""Recursive value redaction shared by broker dispatch and fixed HTTP calls."""

def redact(value, values: set[str]):
    if isinstance(value, str):
        for secret in sorted(values, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {redact(k, values): redact(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, values) for v in value]
    return value

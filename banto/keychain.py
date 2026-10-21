# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Compatibility alias; Keychain implementation lives in banto_core."""
import sys
from ._core import load_module
_implementation = load_module("keychain")


def _require_service():
    from .broker import require_service
    return require_service()


def _observe_secret(value):
    from .broker import observe_secret
    return observe_secret(value)


_implementation.bind_default_hooks(_require_service, _observe_secret)
sys.modules[__name__] = _implementation

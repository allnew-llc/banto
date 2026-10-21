# Copyright (c) 2025-2026 AllNew LLC. All rights reserved.
# Licensed under the banto Dual License; see LICENSE.
"""Compatibility alias for :mod:`banto_core.ecdsa_p256`.

The module identity is shared so legacy monkeypatches affect core globals.
"""

import sys

from ._core import load_module

_implementation = load_module("ecdsa_p256")

sys.modules[__name__] = _implementation

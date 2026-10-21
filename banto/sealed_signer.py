"""Compatibility alias; sealed signing implementation lives in banto_core."""
import sys
from ._core import load_module
_implementation = load_module("sealed_signer")
sys.modules[__name__] = _implementation

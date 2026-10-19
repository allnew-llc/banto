# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Load the shared core from an installed wheel or this source checkout."""
import importlib
import importlib.util
from pathlib import Path
import sys


def load_module(name):
    try:
        importlib.import_module("banto_core")
    except ModuleNotFoundError as error:
        if error.name != "banto_core":
            raise
        source = Path(__file__).resolve().parent.parent / "core/banto_core/__init__.py"
        spec = importlib.util.spec_from_file_location(
            "banto_core", source, submodule_search_locations=[str(source.parent)]
        )
        if spec is None or spec.loader is None:
            raise ImportError("banto_core source package is unavailable") from None
        module = importlib.util.module_from_spec(spec)
        sys.modules["banto_core"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop("banto_core", None)
            raise
    return importlib.import_module("banto_core." + name)

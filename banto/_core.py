# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Load exactly the supported core version before importing its implementation.

Version/location checks prevent accidental deployment drift. Distribution
metadata and source version declarations are not signatures or owner proof.
"""
import ast
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
import sys

EXPECTED_CORE_VERSION = "0.1.0"


def _reject(detail):
    raise ImportError(f"banto_core {EXPECTED_CORE_VERSION} required: {detail}") from None


def _check_source_version(source):
    """Read a literal declaration without executing a potentially wrong core."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError, UnicodeError):
        _reject("source version unavailable")
    versions = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            versions.append(node.value.value if isinstance(node.value, ast.Constant) else None)
    if versions != [EXPECTED_CORE_VERSION]:
        _reject("source version mismatch or missing literal declaration")


def _check_installed_distribution(source):
    try:
        distribution = importlib.metadata.distribution("banto-core")
    except importlib.metadata.PackageNotFoundError:
        _reject("installed distribution metadata missing")
    if distribution.version != EXPECTED_CORE_VERSION:
        _reject("installed distribution version mismatch")
    package_file = "banto_core/__init__.py"
    if not any(str(path) == package_file for path in (distribution.files or ())):
        _reject("installed package record missing")
    if Path(distribution.locate_file(package_file)).resolve() != source:
        _reject("module and distribution locations differ")


def load_module(name):
    root = Path(__file__).resolve().parent.parent
    checkout_source = root / "core/banto_core/__init__.py"
    checkout = (root / "pyproject.toml").is_file() and (root / "core/pyproject.toml").is_file()
    spec = importlib.util.find_spec("banto_core")
    fallback = spec is None
    if fallback:
        if not checkout or not checkout_source.is_file():
            _reject("package and source checkout unavailable")
        source = checkout_source.resolve()
        spec = importlib.util.spec_from_file_location(
            "banto_core", source, submodule_search_locations=[str(source.parent)]
        )
        if spec is None or spec.loader is None:
            _reject("source package loader unavailable")
    else:
        if not spec.origin or spec.submodule_search_locations is None:
            _reject("package source location unavailable")
        source = Path(spec.origin).resolve()
    if not (checkout and source == checkout_source.resolve()):
        _check_installed_distribution(source)
    _check_source_version(source)
    if fallback:
        module = importlib.util.module_from_spec(spec)
        sys.modules["banto_core"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop("banto_core", None)
            raise
    else:
        # A nested ModuleNotFoundError is an implementation failure, never a
        # reason to switch from a discovered installed package to checkout code.
        module = importlib.import_module("banto_core")
    if getattr(module, "__version__", None) != EXPECTED_CORE_VERSION:
        if sys.modules.get("banto_core") is module:
            sys.modules.pop("banto_core", None)
        _reject("loaded module version mismatch")
    return importlib.import_module("banto_core." + name)

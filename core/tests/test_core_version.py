"""Hermetic dummy-package version checks; no install, native APIs or network."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "banto/_core.py"
EXPECTED = "0.1.0"
SCRIPT = """
import ctypes, ctypes.util, importlib.util, json, pathlib, sys
ctypes.util.find_library = lambda name: None
def forbidden(*args, **kwargs):
    raise AssertionError('native framework loading forbidden')
ctypes.cdll.LoadLibrary = forbidden
adapter_path, site_path = map(pathlib.Path, sys.argv[1:3])
sys.path.insert(0, str(site_path))
spec = importlib.util.spec_from_file_location('isolated_core_adapter', adapter_path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
try:
    result = adapter.load_module('fixture_leaf')
except Exception as error:
    print(json.dumps({'ok': False, 'type': type(error).__name__, 'name': getattr(error, 'name', None),
                      'error': str(error), 'loaded': 'banto_core' in sys.modules}))
else:
    print(json.dumps({'ok': True, 'value': result.VALUE, 'file': result.__file__}))
"""


@pytest.fixture
def sandbox(tmp_path):
    app = tmp_path / "app"
    adapter = app / "banto/_core.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(ADAPTER.read_text())
    site = tmp_path / "site"
    site.mkdir()
    marker = tmp_path / "imported"

    def package(directory, version=EXPECTED, body="", value="installed"):
        directory.mkdir(parents=True, exist_ok=True)
        declaration = "" if version is None else f"__version__ = {version!r}\n"
        directory.joinpath("__init__.py").write_text(
            declaration + f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + body)
        directory.joinpath("fixture_leaf.py").write_text(f"VALUE = {value!r}\n")

    def distribution(version=EXPECTED, record=True, directory=site):
        info = directory / "banto_core-0.1.0.dist-info"
        info.mkdir(parents=True, exist_ok=True)
        info.joinpath("METADATA").write_text(f"Metadata-Version: 2.1\nName: banto-core\nVersion: {version}\n")
        if record:
            info.joinpath("RECORD").write_text("banto_core/__init__.py,,\nbanto_core/fixture_leaf.py,,\n")

    def checkout(version=EXPECTED, body=""):
        app.joinpath("pyproject.toml").write_text('[project]\nname = "banto"\n')
        core = app / "core"
        core.mkdir(exist_ok=True)
        core.joinpath("pyproject.toml").write_text(f'[project]\nname = "banto-core"\nversion = "{EXPECTED}"\n')
        package(core / "banto_core", version, body, "checkout")

    def run(search=site):
        process = subprocess.run([sys.executable, "-B", "-I", "-S", "-c", SCRIPT,
                                  str(adapter), str(search)], cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=15, check=False)
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    return dict(app=app, site=site, marker=marker, package=package, distribution=distribution,
                checkout=checkout, run=run)


def test_expected_version_matches_both_project_metadata():
    assert f'"banto-core=={EXPECTED}"' in ROOT.joinpath("pyproject.toml").read_text()
    assert f'version = "{EXPECTED}"' in ROOT.joinpath("core/pyproject.toml").read_text()
    assert f'EXPECTED_CORE_VERSION = "{EXPECTED}"' in ADAPTER.read_text()


def test_installed_core_loads_without_checkout(sandbox):
    sandbox["package"](sandbox["site"] / "banto_core")
    sandbox["distribution"]()
    result = sandbox["run"]()
    assert result["ok"] and result["value"] == "installed"
    assert sandbox["marker"].exists()


@pytest.mark.parametrize("version", ["9.9.9", "0.1.0+local", ""])
def test_wrong_installed_metadata_is_rejected_before_import(sandbox, version):
    sandbox["package"](sandbox["site"] / "banto_core")
    sandbox["distribution"](version)
    result = sandbox["run"]()
    assert not result["ok"] and "distribution version mismatch" in result["error"]
    assert not sandbox["marker"].exists()


def test_installed_metadata_missing_does_not_escape_to_checkout(sandbox):
    sandbox["package"](sandbox["site"] / "banto_core")
    sandbox["checkout"]()
    result = sandbox["run"]()
    assert not result["ok"] and "distribution metadata missing" in result["error"]
    assert not sandbox["marker"].exists()


@pytest.mark.parametrize("version", ["9.9.9", None])
def test_module_distribution_version_spoof_is_rejected_before_import(sandbox, version):
    sandbox["package"](sandbox["site"] / "banto_core", version)
    sandbox["distribution"]()
    result = sandbox["run"]()
    assert not result["ok"] and "source version mismatch" in result["error"]
    assert not sandbox["marker"].exists()


def test_missing_package_record_is_rejected_before_import(sandbox):
    sandbox["package"](sandbox["site"] / "banto_core")
    sandbox["distribution"](record=False)
    result = sandbox["run"]()
    assert not result["ok"] and "package record missing" in result["error"]
    assert not sandbox["marker"].exists()


def test_shadow_package_cannot_borrow_other_distribution_metadata(sandbox):
    sandbox["checkout"]()
    sandbox["package"](sandbox["site"] / "banto_core")
    sandbox["distribution"](directory=sandbox["app"] / "core")
    # Add the distribution location without selecting that location's package.
    original_script = SCRIPT.replace("sys.path.insert(0, str(site_path))",
        "sys.path.insert(0, str(site_path)); sys.path.append(str(adapter_path.parents[1] / 'core'))")
    process = subprocess.run([sys.executable, "-B", "-I", "-S", "-c", original_script,
                              str(sandbox["app"] / "banto/_core.py"), str(sandbox["site"])],
        env={"PATH": os.defpath}, capture_output=True, text=True, timeout=15, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert not result["ok"] and "locations differ" in result["error"]
    assert not sandbox["marker"].exists()


def test_missing_package_and_checkout_fail_closed(sandbox):
    result = sandbox["run"]()
    assert not result["ok"] and "package and source checkout unavailable" in result["error"]


def test_missing_installed_package_uses_checked_source_fallback(sandbox):
    sandbox["checkout"]()
    result = sandbox["run"]()
    assert result["ok"] and result["value"] == "checkout"


def test_importable_checkout_is_the_only_metadata_free_exception(sandbox):
    sandbox["checkout"]()
    result = sandbox["run"](sandbox["app"] / "core")
    assert result["ok"] and result["value"] == "checkout"


def test_adjacent_source_without_checkout_metadata_is_not_a_fallback(sandbox):
    sandbox["checkout"]()
    sandbox["app"].joinpath("core/pyproject.toml").unlink()
    result = sandbox["run"]()
    assert not result["ok"] and "package and source checkout unavailable" in result["error"]
    assert not sandbox["marker"].exists()


@pytest.mark.parametrize("version", ["9.9.9", None])
def test_wrong_source_fallback_version_is_rejected_before_import(sandbox, version):
    sandbox["checkout"](version)
    result = sandbox["run"]()
    assert not result["ok"] and "source version mismatch" in result["error"]
    assert not sandbox["marker"].exists()


@pytest.mark.parametrize("installed", [False, True])
def test_nested_module_not_found_is_preserved_without_fallback(sandbox, installed):
    body = "import nonexistent_core_fixture_dependency\n"
    sandbox["checkout"](body="" if installed else body)
    if installed:
        sandbox["package"](sandbox["site"] / "banto_core", body=body)
        sandbox["distribution"]()
    result = sandbox["run"]()
    assert not result["ok"] and result["type"] == "ModuleNotFoundError"
    assert result["name"] == "nonexistent_core_fixture_dependency"
    assert not result["loaded"]


@pytest.mark.parametrize("installed", [False, True])
def test_runtime_version_mutation_is_also_rejected(sandbox, installed):
    body = "globals()['__version__'] = '9.9.9'\n"
    if installed:
        sandbox["package"](sandbox["site"] / "banto_core", body=body)
        sandbox["distribution"]()
    else:
        sandbox["checkout"](body=body)
    result = sandbox["run"]()
    assert not result["ok"] and "loaded module version mismatch" in result["error"]
    assert not result["loaded"]

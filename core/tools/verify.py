"""Local verification; never install or change a running service."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def source_checks():
    manifest = json.loads((ROOT / "core/compatibility-tests.json").read_text())
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((ROOT / "tests").rglob("*"))
             if path.is_file() and "__pycache__" not in path.parts}
    if files != manifest["files"]:
        raise ValueError("existing tests changed")
    license_bytes = (ROOT / "LICENSE").read_bytes()
    if (ROOT / "core/LICENSE").read_bytes() != license_bytes:
        raise ValueError("core license differs from Banto")
    if hashlib.sha256(license_bytes).hexdigest() != manifest["license_sha256"]:
        raise ValueError("Banto license changed")
    counts = {}
    for path in sorted((ROOT / "core/banto_core").glob("*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        counts[path.name] = len(source.splitlines())
        if counts[path.name] > 400:
            raise ValueError("core source file exceeds 400 lines: " + path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [entry.name for entry in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            else:
                continue
            if any(name == "banto" or name.startswith("banto.") for name in names):
                raise ValueError("core imports Banto application")
    return {"existing_test_files": len(files), "existing_tests_unchanged": True,
            "license_unchanged": True, "core_python_lines": sum(counts.values()),
            "core_file_lines": counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    result = source_checks()
    state = ROOT / "work/verification"
    state.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "core"),
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    code = subprocess.call([sys.executable, "-m", "pytest", "core/tests", "-q",
                            "-p", "no:cacheprovider"], cwd=ROOT, env=environment)
    result["core_tests_exit_code"] = code
    if code == 0 and not args.core_only:
        code = subprocess.call([sys.executable, str(ROOT / "core/tools/legacy_tests.py"),
                                "--run", "--state", str(state / "legacy-state"),
                                "--json", str(state / "legacy.json"),
                                "--log", str(state / "legacy.log")], cwd=ROOT)
        result["legacy_exit_code"] = code
    else:
        result["legacy_exit_code"] = None
    result["seconds"] = round(time.monotonic() - started, 3)
    result["phase3_c5_complete"] = False  # physical signer tests require owner verification
    (state / "verify.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

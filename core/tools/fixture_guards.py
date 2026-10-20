"""Pure allowlist checks for accidental subprocess side effects in legacy tests.

These checks inspect paths and supplied audit-event data; they never start a
process. They are not OS isolation against deliberately hostile same-UID code.
"""
import os
from pathlib import Path
import re


SYSTEM_TOOLS = {
    "echo": Path("/bin/echo"),
    "false": Path("/usr/bin/false"),
    "touch": Path("/usr/bin/touch"),
    "sh": Path("/bin/sh"),
    "git": Path("/usr/bin/git"),
}
SYSTEM_DIRECTORIES = frozenset({"/bin", "/usr/bin", "/usr/sbin", "/sbin"})
PYTHON_ENV = frozenset({"PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"})
SHELL_ENV = frozenset({"ENV", "BASH_ENV", "IFS", "SHELLOPTS", "BASHOPTS", "CDPATH", "ZDOTDIR"})


def normalized_absolute(value):
    """Reject lexical traversal/normalization tricks before resolving symlinks."""
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    text = os.fsdecode(value)
    if not text or "\x00" in text or not text.startswith("/") or text.startswith("//"):
        return None
    if any(part in {"", ".", ".."} for part in text[1:].split("/")):
        return None
    return Path(text)


def fixture_path(value, fixture_root, filename):
    """Unquoted shell paths must contain ASCII filename characters only."""
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        return False
    path = normalized_absolute(value)
    if path is None or path.name != filename:
        return False
    resolved = path.resolve()
    return resolved == path and resolved.is_relative_to(Path(fixture_root).resolve())


def fixture_command(values, environment, fixture_root):
    """Only the fixed dummy lease commands; no shell syntax inside path slots."""
    if (environment or {}).get("BANTO_LEASE_VALUE", "") not in {"", "FAKE", "FAKE_LEASE_VALUE"}:
        return False
    if values in [["echo", value] for value in ("FAKE", "FAKE_LEASE_VALUE", "A", "B")]:
        return True
    if values == ["false"]:
        return True
    if len(values) == 2 and values[0] == "touch":
        return fixture_path(values[1], fixture_root, "ran")
    if len(values) == 3 and values[:2] == ["/bin/sh", "-c"]:
        for pattern, filename in (
            (r"sh -c 'true' && printf %s \"\$BANTO_LEASE_VALUE\" > (\S+)", "revoked.txt"),
            (r"test -e (\S+) && echo \"\$BANTO_LEASE_VALUE\" >/dev/null", "allow"),
        ):
            match = re.fullmatch(pattern, values[2])
            if match:
                return fixture_path(match[1], fixture_root, filename)
    return False


def safe_environment(environment):
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                for k, v in environment.items()):
        return False
    if any("\x00" in key or "\x00" in value for key, value in environment.items()):
        return False
    for key in environment:
        upper = key.upper()
        if (upper.startswith(("LD_", "DYLD_", "GIT_", "BASH_FUNC_")) or upper in SHELL_ENV
                or (upper.startswith("PYTHON") and upper not in PYTHON_ENV)):
            return False
    paths = environment.get("PATH", os.defpath).split(os.pathsep)
    return bool(paths) and all(path in SYSTEM_DIRECTORIES for path in paths)


def resolve_executable(value, environment):
    """Resolve a bare tool only through the effective, fixed system PATH."""
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    text = os.fsdecode(value)
    if "/" in text:
        path = normalized_absolute(text)
        return path.resolve() if path is not None else None
    if text not in SYSTEM_TOOLS:
        return None
    for directory in environment.get("PATH", os.defpath).split(os.pathsep):
        path = Path(directory) / text
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def git_check_ignore(values, cwd, fixture_root, repo):
    # Repository configuration must not start an fsmonitor helper during a check.
    prefix = ["git", "-c", "core.fsmonitor=false", "check-ignore"]
    if len(values) < 5 or values[:4] != prefix:
        return False
    paths = []
    operands = False
    for value in values[4:]:
        if not operands and value in {"-q", "--quiet"}:
            continue
        if not operands and value == "--":
            operands = True
            continue
        if value.startswith("-"):
            return False
        operands = True
        path = Path(value)
        if any(part in {".", ".."} for part in value.split("/")):
            return False
        path = (cwd / path).resolve()
        if not (path.is_relative_to(fixture_root) or path.is_relative_to(repo)):
            return False
        paths.append(path)
    return bool(paths)


def subprocess_allowed(executable, command, cwd, environment, *, fixture_root, repo,
                       python_executable, inherited_environment, inherited_cwd):
    """Validate the actual executable, argv, cwd and effective environment together."""
    try:
        effective = dict(inherited_environment) if environment is None else environment
        if not safe_environment(effective) or not isinstance(command, (list, tuple)) or not command:
            return False
        if any(not isinstance(value, (str, bytes)) for value in command):
            return False
        values = [os.fsdecode(value) for value in command]
        if any("\x00" in value for value in values):
            return False
        directory = normalized_absolute(inherited_cwd if cwd is None else cwd)
        if directory is None:
            return False
        directory = directory.resolve()
        fixture_root, repo = Path(fixture_root).resolve(), Path(repo).resolve()
        if not (directory.is_relative_to(fixture_root) or directory.is_relative_to(repo)):
            return False
        actual = resolve_executable(executable, effective)
        declared = resolve_executable(values[0], effective)
        if actual is None or actual != declared:
            return False
        python = Path(python_executable).resolve()
        if actual == python:
            return (directory == repo and len(values) == 4 and values[1:3] == ["-m", "banto"]
                    and values[3] in {"--version", "-V"})
        name = Path(values[0]).name
        if name not in SYSTEM_TOOLS or actual != SYSTEM_TOOLS[name].resolve():
            return False
        # Match the reviewed fixture grammar after verifying absolute tool aliases.
        values[0] = "/bin/sh" if name == "sh" else name
        return (git_check_ignore(values, directory, fixture_root, repo) if name == "git"
                else fixture_command(values, effective, fixture_root))
    except (OSError, RuntimeError, TypeError, ValueError):
        return False

"""Docs must match the code (counts, tables, versions)."""
import json
import re
import sys
from pathlib import Path

import pytest

import banto
from banto.broker import MCP_OPERATIONS
from banto.sync.cli import REMOVED_SYNC_COMMANDS, SYNC_COMMANDS

ROOT = Path(__file__).resolve().parent.parent
READMES = [ROOT / "README.md", ROOT / "README.ja.md"]
BROKER_ONLY_TOOLS = {"banto_api_request", "banto_broker_health"}

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _table_names(text: str, pattern: str) -> list[str]:
    return re.findall(pattern, text, flags=re.MULTILINE)


def test_versions_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert banto.__version__ == pyproject
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [p["version"] for p in lock["package"] if p["name"] == "banto"]
    assert locked == [pyproject]
    headings = re.findall(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)
    released = [item for item in headings if item.lower() != "unreleased"]
    assert released and released[0] == pyproject


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_readme_mcp_tool_table(readme):
    names = _table_names(readme.read_text(), r"^\| `(banto_[a-z_]+)` \|")
    assert len(names) == len(set(names))
    assert set(names) == set(MCP_OPERATIONS) | BROKER_ONLY_TOOLS
    assert f"{len(MCP_OPERATIONS)}%20tools%20({len(MCP_OPERATIONS) + len(BROKER_ONLY_TOOLS)}%20via%20broker)" in readme.read_text()


def test_claude_md_mcp_tool_table():
    text = (ROOT / "CLAUDE.md").read_text()
    names = _table_names(text, r"^\| `(banto_[a-z_]+)` \|")
    assert set(names) == set(MCP_OPERATIONS) | BROKER_ONLY_TOOLS
    assert "`banto lease get`" in text  # listed under prohibited operations


def test_mcp_server_registers_exactly_mcp_operations():
    pytest.importorskip("mcp")
    from banto import mcp_server
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == set(MCP_OPERATIONS)
    from banto.broker_mcp import build_mcp
    broker_tools = {tool.name for tool in build_mcp()._tool_manager.list_tools()}
    assert broker_tools == set(MCP_OPERATIONS) | BROKER_ONLY_TOOLS


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_readme_sync_command_table(readme):
    text = readme.read_text()
    names = _table_names(text, r"^\| `banto sync ([a-z-]+)")
    documented = set(SYNC_COMMANDS) - REMOVED_SYNC_COMMANDS
    assert len(names) == len(set(names))
    assert set(names) == documented
    assert re.search(rf"<strong>Sync[^<]*\({len(documented)}\)</strong>", text)
    for removed in REMOVED_SYNC_COMMANDS:
        assert f"banto sync {removed}" not in text


def test_sync_help_lists_every_command(capsys):
    from banto.sync.cli import cmd_sync_dispatch
    with pytest.raises(SystemExit):
        cmd_sync_dispatch(["--help"])
    out = capsys.readouterr().out
    for name in set(SYNC_COMMANDS) - REMOVED_SYNC_COMMANDS:
        assert re.search(rf"^  {re.escape(name)}\b", out, re.MULTILINE), name


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_readme_has_no_stale_claims(readme):
    text = readme.read_text()
    assert "no daemon" not in text and "デーモン" not in text
    assert "passed-0D3B66" not in text  # static test-count badge
    assert "actions/workflows/ci.yml/badge.svg" in text
    assert "api_request_rpm" in text


def test_bundled_config_documents_broker_rpm():
    config = json.loads((ROOT / "banto" / "config.json").read_text())
    assert config["broker"]["api_request_rpm"] == 30

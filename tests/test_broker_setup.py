import json
from pathlib import Path

import pytest

from banto.broker_setup import apply, plan, toml_update, update_rules, START


def test_defaults_preserve_other_tools_settings_and_are_idempotent(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    (home / ".gemini").mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / ".gemini/settings.json").write_text(json.dumps({
        "model": {"name": "user-selected"}, "mcpServers": {"existing": {"command": "fixture"}},
        "security": {"auth": {"selectedType": "user-choice"}},
    }))
    (home / ".codex/config.toml").write_text('model = "user-selected"\nmodel_reasoning_effort = "high"\n')
    (home / ".codex/AGENTS.md").write_text("Existing user policy.\n")
    command = Path("/fixture/banto/run-banto-mcp.sh")
    changes = plan(home, workspace, command)
    apply(changes)
    assert not plan(home, workspace, command)
    data = json.loads((home / ".gemini/settings.json").read_text())
    assert data["model"]["name"] == "user-selected"
    assert data["mcpServers"]["existing"] == {"command": "fixture"}
    assert data["security"]["auth"]["selectedType"] == "user-choice"
    assert data["mcpServers"]["banto"] == {"command": str(command), "args": []}
    assert (home / ".codex/config.toml").read_text().startswith('model = "user-selected"')
    assert (home / ".codex/AGENTS.md").read_text().startswith("Existing user policy.")
    assert (home / ".codex/AGENTS.md").read_text().count(START) == 1


def test_conflicting_codex_entry_does_not_discard_existing_auth():
    with pytest.raises(ValueError, match="review"):
        toml_update('[mcp_servers.banto]\ncommand = "other"\n', "/fixture/run")


def test_malformed_managed_rules_fail_closed():
    with pytest.raises(ValueError):
        update_rules(START)


def test_active_claude_profile_receives_server_and_policy(tmp_path):
    home, workspace, profile = tmp_path / "home", tmp_path / "workspace", tmp_path / "profile"
    profile.mkdir()
    (profile / ".claude.json").write_text('{"model":"preserve-me"}')
    (profile / "CLAUDE.md").write_text("Existing profile policy.\n")
    command = Path("/fixture/run-banto-mcp.sh")
    apply(plan(home, workspace, command, claude_config_dir=profile))
    actual = json.loads((profile / ".claude.json").read_text())
    assert actual["model"] == "preserve-me"
    assert actual["mcpServers"]["banto"]["command"] == str(command)
    assert (profile / "CLAUDE.md").read_text().startswith("Existing profile policy.")
    assert START in (profile / "CLAUDE.md").read_text()
    assert not plan(home, workspace, command, claude_config_dir=profile)

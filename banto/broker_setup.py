"""Configure agent defaults without exposing existing config contents.

Only the banto entry and an explicit managed instruction block are changed.
Other tools, credentials, models, approval settings and reasoning are preserved.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

START = "<!-- BEGIN banto-broker-default -->"
END = "<!-- END banto-broker-default -->"
RULE = """## Keychain利用の既定経路

Keychainに登録済みの秘密値を利用・登録する場合は、共通bantoサービスを既定とする。
- MCPの `banto` ツール、または `banto.broker_client.BrokerClient` から操作を依頼する。
- 秘密値そのものを返す操作、`get_key()` / `KeychainStore.get()`、`security ... -w`、
  Security.frameworkの直接呼び出し、環境変数への秘密値の取り出しを使用しない。
- 登録は `banto_register_key` の本人用ローカル画面を使う。チャットに秘密値を入力させない。
- 未対応の操作はbantoの許可された操作として実装・検証してから利用する。
  banto停止・未対応・認証エラー時に直接読み取りへ迂回しない。
- API送信・課金・外部変更は元の承認範囲と予算を守る。登録されたキーの一覧・値を一括取得しない。
- macOSのパスワード、Touch ID、MFA等の本人確認はユーザーが操作する。
"""


def update_rules(text: str) -> str:
    block = START + "\n" + RULE + END
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
            raise ValueError("invalid managed instruction block")
        return text[:text.index(START)] + block + text[text.index(END) + len(END):]
    return text.rstrip() + "\n\n" + block + "\n"


def json_update(text: str, entry: dict) -> str:
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("invalid MCP configuration")
    data.setdefault("mcpServers", {})["banto"] = entry
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def toml_update(text: str, command: str) -> str:
    import tomllib
    parsed = tomllib.loads(text)
    entry = {"command": command, "args": [], "enabled": True}
    existing = parsed.get("mcp_servers", {}).get("banto")
    if existing == entry:
        return text
    if existing is not None:
        # Never discard auth headers or nested tables from an existing entry.
        raise ValueError("existing Codex banto entry differs; review it before replacing")
    block = '\n[mcp_servers.banto]\ncommand = ' + json.dumps(command) + '\nargs = []\nenabled = true\n'
    result = text.rstrip() + "\n" + block
    assert tomllib.loads(result)["mcp_servers"]["banto"] == entry
    return result


def plan(home: Path, workspace: Path, command: Path) -> dict[Path, str]:
    if not command.is_absolute():
        raise ValueError("launcher must be absolute")
    entry = {"command": str(command), "args": []}
    changes = {}
    json_paths = [workspace / "mcp/.mcp.json", workspace / ".gemini/settings.json",
                  workspace / "mcp_config.json", home / ".claude.json",
                  home / ".gemini/settings.json", home / ".gemini/antigravity/mcp_config.json"]
    for path in json_paths:
        old = path.read_text() if path.exists() else ""
        # Work on the resolved path so symlinked configs retain their aliases.
        changes[path.resolve()] = json_update(old, entry)
    path = home / ".codex/config.toml"
    changes[path.resolve()] = toml_update(path.read_text() if path.exists() else "", str(command))
    for rel in (".codex/AGENTS.md", ".claude/CLAUDE.md", ".gemini/GEMINI.md"):
        path = home / rel
        changes[path.resolve()] = update_rules(path.read_text() if path.exists() else "")
    return {path: text for path, text in changes.items() if not path.exists() or path.read_text() != text}


def apply(changes: dict[Path, str]) -> None:
    for path, text in changes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, name = tempfile.mkstemp(prefix=".banto-config-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(text)
            os.chmod(name, mode)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        changes = plan(Path.home(), args.workspace, args.launcher)
        if args.apply:
            if not args.launcher.is_file() or not os.access(args.launcher, os.X_OK):
                raise ValueError("launcher not executable")
            apply(changes)
        print(json.dumps({"applied": args.apply, "paths": [str(p) for p in changes]}, ensure_ascii=False))
    except (ValueError, OSError):
        raise SystemExit("Default configuration failed; config values suppressed. Review the target structure.") from None


if __name__ == "__main__":
    main()

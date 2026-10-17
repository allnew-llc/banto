# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for `banto --version` / `-V` / `version`."""

import subprocess
import sys
from unittest.mock import Mock

import pytest

import banto
from banto import __main__ as cli
from banto import broker_cli
from banto.broker_client import BrokerClient

FLAGS = ["--version", "-V", "version"]


@pytest.mark.parametrize("flag", FLAGS)
def test_version_prints_and_exits_zero(flag, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["banto", flag])
    cli.main()  # returns normally (exit status 0)
    assert capsys.readouterr().out == f"banto {banto.__version__}\n"


@pytest.mark.parametrize("flag", FLAGS)
def test_version_works_in_broker_mode(flag, tmp_path, monkeypatch, capsys):
    (tmp_path / "required").touch()
    monkeypatch.setattr(broker_cli, "runtime_dir", lambda: tmp_path)
    call = Mock(side_effect=AssertionError("broker must not be called"))
    monkeypatch.setattr(BrokerClient, "call", call)
    monkeypatch.setattr(sys, "argv", ["banto", flag])
    cli.main()
    assert capsys.readouterr().out == f"banto {banto.__version__}\n"
    call.assert_not_called()


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_subprocess_exit_code(flag):
    proc = subprocess.run(
        [sys.executable, "-m", "banto", flag], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"banto {banto.__version__}"


def test_help_lists_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["banto", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--version" in out
    assert "version " in out


def test_version_is_registered_command():
    assert "version" in cli.COMMANDS

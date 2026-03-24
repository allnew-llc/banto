# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto sync notification integrations."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from banto.sync.config import NotifierConfig, SyncConfig
from banto.sync.notifiers import NOTIFIER_MAP, get_notifier
from banto.sync.notifiers.base import EventPayload, SyncEvent
from banto.sync.notifiers.slack import SlackNotifier
from banto.sync.notifiers.teams import TeamsNotifier
from banto.sync.sync import SyncReport, SyncResult, fire_notifications


class TestNotifierRegistry:
    def test_all_notifiers_registered(self):
        assert "slack" in NOTIFIER_MAP
        assert "teams" in NOTIFIER_MAP
        assert "datadog" in NOTIFIER_MAP
        assert "pagerduty" in NOTIFIER_MAP

    def test_get_notifier(self):
        n = get_notifier("slack", "https://hooks.slack.com/test")
        assert isinstance(n, SlackNotifier)
        assert n.webhook_url == "https://hooks.slack.com/test"

    def test_get_notifier_unknown(self):
        import pytest
        with pytest.raises(ValueError, match="Unknown notifier"):
            get_notifier("unknown", "url")


class TestEventPayload:
    def test_payload_never_contains_values(self):
        p = EventPayload(
            event=SyncEvent.SYNC_OK,
            secret_name="openai",
            targets=["vercel:app"],
            ok_count=1,
            fail_count=0,
        )
        # Verify no value field exists
        assert not hasattr(p, "value")
        assert not hasattr(p, "secret_value")


class TestSlackNotifier:
    @patch("banto.sync.notifiers.slack.subprocess.run")
    def test_notify_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="ok"
        )
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        result = notifier.notify(EventPayload(
            event=SyncEvent.SYNC_FAIL,
            secret_name="openai",
            targets=["vercel:app", "cf:site"],
            ok_count=1,
            fail_count=1,
        ))
        assert result is True
        # Verify curl was called with the webhook URL
        cmd = mock_run.call_args[0][0]
        assert "https://hooks.slack.com/test" in cmd


class TestTeamsNotifier:
    @patch("banto.sync.notifiers.teams.subprocess.run")
    def test_notify_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        notifier = TeamsNotifier(webhook_url="https://outlook.webhook.office.com/test")
        result = notifier.notify(EventPayload(
            event=SyncEvent.AUDIT_DRIFT,
            secret_name="gemini",
            ok_count=0,
            fail_count=2,
        ))
        assert result is True


class TestFireNotifications:
    @patch("banto.sync.notifiers.get_notifier")
    def test_fires_for_matching_events(self, mock_get):
        mock_notifier = type("MockNotifier", (), {"notify": lambda self, p: True})()
        mock_get.return_value = mock_notifier

        config = SyncConfig(notifiers=[
            NotifierConfig(name="slack", webhook_url="url", events=["sync_fail"]),
        ])
        report = SyncReport(results=[
            SyncResult(secret_name="x", target_label="t", success=False),
        ])
        fire_notifications(config, SyncEvent.SYNC_FAIL, report, secret_name="x")
        mock_get.assert_called_once_with("slack", "url")

    @patch("banto.sync.notifiers.get_notifier")
    def test_skips_non_matching_events(self, mock_get):
        config = SyncConfig(notifiers=[
            NotifierConfig(name="slack", webhook_url="url", events=["sync_fail"]),
        ])
        report = SyncReport(results=[
            SyncResult(secret_name="x", target_label="t", success=True),
        ])
        fire_notifications(config, SyncEvent.SYNC_OK, report)
        mock_get.assert_not_called()

    def test_no_notifiers_configured(self):
        """Should not raise when no notifiers are configured"""
        config = SyncConfig()
        report = SyncReport()
        fire_notifications(config, SyncEvent.SYNC_OK, report)  # no-op


class TestNotifierConfig:
    def test_from_dict(self):
        nc = NotifierConfig.from_dict({
            "name": "slack",
            "webhook_url": "https://hooks.slack.com/xxx",
            "events": ["sync_fail", "rotate"],
        })
        assert nc.name == "slack"
        assert nc.events == ["sync_fail", "rotate"]

    def test_default_events(self):
        nc = NotifierConfig.from_dict({"name": "pagerduty", "webhook_url": "key"})
        assert "sync_fail" in nc.events
        assert "audit_drift" in nc.events

    def test_roundtrip(self):
        nc = NotifierConfig(name="teams", webhook_url="url", events=["add"])
        d = nc.to_dict()
        restored = NotifierConfig.from_dict(d)
        assert restored.name == nc.name
        assert restored.events == nc.events


class TestSyncConfigWithNotifiers:
    def test_load_save_with_notifiers(self, tmp_path):
        cfg_path = tmp_path / "sync.json"
        config = SyncConfig(notifiers=[
            NotifierConfig(name="slack", webhook_url="https://hook", events=["sync_fail"]),
        ])
        config.save(cfg_path)
        loaded = SyncConfig.load(cfg_path)
        assert len(loaded.notifiers) == 1
        assert loaded.notifiers[0].name == "slack"

    def test_load_without_notifiers(self, tmp_path):
        cfg_path = tmp_path / "sync.json"
        SyncConfig().save(cfg_path)
        loaded = SyncConfig.load(cfg_path)
        assert loaded.notifiers == []

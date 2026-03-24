# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Slack webhook notifier for vault events."""
from __future__ import annotations

import json
import subprocess

from .base import EventPayload, Notifier, SyncEvent

_EMOJI = {
    SyncEvent.SYNC_OK: ":white_check_mark:",
    SyncEvent.SYNC_FAIL: ":x:",
    SyncEvent.ROTATE: ":arrows_counterclockwise:",
    SyncEvent.ADD: ":heavy_plus_sign:",
    SyncEvent.REMOVE: ":heavy_minus_sign:",
    SyncEvent.AUDIT_DRIFT: ":warning:",
    SyncEvent.AUDIT_OK: ":shield:",
}


class SlackNotifier(Notifier):
    """Send vault event notifications to Slack via incoming webhook."""

    def notify(self, payload: EventPayload) -> bool:
        emoji = _EMOJI.get(payload.event, ":bell:")
        title = f"{emoji} andon vault: {payload.event.value}"

        fields = [f"*Secret:* `{payload.secret_name}`"]
        if payload.targets:
            fields.append(f"*Targets:* {', '.join(payload.targets)}")
        if payload.ok_count or payload.fail_count:
            fields.append(f"*Result:* {payload.ok_count} OK, {payload.fail_count} failed")
        if payload.message:
            fields.append(f"*Detail:* {payload.message}")

        slack_payload = json.dumps({
            "text": title,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{title}\n" + "\n".join(fields)},
                },
            ],
        })

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", "Content-Type: application/json",
                "-d", slack_payload,
                self.webhook_url,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "ok"

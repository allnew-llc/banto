# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Microsoft Teams webhook notifier for vault events."""
from __future__ import annotations

import json
import subprocess

from .base import EventPayload, Notifier, SyncEvent

_COLOR = {
    SyncEvent.SYNC_OK: "00ff00",
    SyncEvent.SYNC_FAIL: "ff0000",
    SyncEvent.ROTATE: "0078d4",
    SyncEvent.ADD: "00ff00",
    SyncEvent.REMOVE: "ffa500",
    SyncEvent.AUDIT_DRIFT: "ff0000",
    SyncEvent.AUDIT_OK: "00ff00",
}


class TeamsNotifier(Notifier):
    """Send vault event notifications to Microsoft Teams via incoming webhook."""

    def notify(self, payload: EventPayload) -> bool:
        color = _COLOR.get(payload.event, "808080")

        facts = [{"name": "Secret", "value": f"`{payload.secret_name}`"}]
        if payload.targets:
            facts.append({"name": "Targets", "value": ", ".join(payload.targets)})
        if payload.ok_count or payload.fail_count:
            facts.append({
                "name": "Result",
                "value": f"{payload.ok_count} OK, {payload.fail_count} failed",
            })
        if payload.message:
            facts.append({"name": "Detail", "value": payload.message})

        teams_payload = json.dumps({
            "@type": "MessageCard",
            "themeColor": color,
            "summary": f"andon vault: {payload.event.value}",
            "sections": [{
                "activityTitle": f"andon vault: {payload.event.value}",
                "facts": facts,
            }],
        })

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", "Content-Type: application/json",
                "-d", teams_payload,
                self.webhook_url,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

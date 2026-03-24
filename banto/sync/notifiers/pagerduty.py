# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""PagerDuty events notifier for vault events."""
from __future__ import annotations

import json
import subprocess

from .base import EventPayload, Notifier, SyncEvent

_SEVERITY = {
    SyncEvent.SYNC_OK: "info",
    SyncEvent.SYNC_FAIL: "error",
    SyncEvent.ROTATE: "info",
    SyncEvent.ADD: "info",
    SyncEvent.REMOVE: "warning",
    SyncEvent.AUDIT_DRIFT: "critical",
    SyncEvent.AUDIT_OK: "info",
}


class PagerDutyNotifier(Notifier):
    """Send vault events to PagerDuty via Events API v2.

    webhook_url is the PagerDuty integration key (routing key).
    Only SYNC_FAIL and AUDIT_DRIFT create incidents; others resolve or are informational.
    """

    def notify(self, payload: EventPayload) -> bool:
        severity = _SEVERITY.get(payload.event, "info")

        # Only trigger incidents for failures
        if payload.event in (SyncEvent.SYNC_FAIL, SyncEvent.AUDIT_DRIFT):
            event_action = "trigger"
        elif payload.event in (SyncEvent.SYNC_OK, SyncEvent.AUDIT_OK):
            event_action = "resolve"
        else:
            event_action = "trigger"

        custom_details = {
            "secret": payload.secret_name,
            "event": payload.event.value,
            "ok_count": payload.ok_count,
            "fail_count": payload.fail_count,
        }
        if payload.targets:
            custom_details["targets"] = payload.targets
        if payload.message:
            custom_details["message"] = payload.message

        pd_payload = json.dumps({
            "routing_key": self.webhook_url,
            "event_action": event_action,
            "dedup_key": f"andon-vault-{payload.secret_name}",
            "payload": {
                "summary": f"andon vault: {payload.event.value} — {payload.secret_name}",
                "severity": severity,
                "source": "andon-vault",
                "custom_details": custom_details,
            },
        })

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", "Content-Type: application/json",
                "-d", pd_payload,
                "https://events.pagerduty.com/v2/enqueue",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"success"' in result.stdout

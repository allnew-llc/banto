# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Datadog events notifier for vault events."""
from __future__ import annotations

import json
import subprocess

from .base import EventPayload, Notifier, SyncEvent

_ALERT_TYPE = {
    SyncEvent.SYNC_OK: "success",
    SyncEvent.SYNC_FAIL: "error",
    SyncEvent.ROTATE: "info",
    SyncEvent.ADD: "info",
    SyncEvent.REMOVE: "warning",
    SyncEvent.AUDIT_DRIFT: "warning",
    SyncEvent.AUDIT_OK: "success",
}


class DatadogNotifier(Notifier):
    """Send vault events to Datadog via Events API.

    webhook_url should be the Datadog API key (not a webhook URL).
    Events are posted to `https://api.datadoghq.com/api/v1/events`.
    """

    def notify(self, payload: EventPayload) -> bool:
        alert_type = _ALERT_TYPE.get(payload.event, "info")
        tags = [
            "source:andon-vault",
            f"secret:{payload.secret_name}",
            f"event:{payload.event.value}",
        ]
        for target in payload.targets:
            tags.append(f"target:{target}")

        text_parts = [f"Secret: {payload.secret_name}"]
        if payload.targets:
            text_parts.append(f"Targets: {', '.join(payload.targets)}")
        if payload.ok_count or payload.fail_count:
            text_parts.append(f"Result: {payload.ok_count} OK, {payload.fail_count} failed")
        if payload.message:
            text_parts.append(f"Detail: {payload.message}")

        dd_payload = json.dumps({
            "title": f"andon vault: {payload.event.value}",
            "text": "\n".join(text_parts),
            "alert_type": alert_type,
            "tags": tags,
            "source_type_name": "andon",
        })

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "-H", "Content-Type: application/json",
                "-H", f"DD-API-KEY: {self.webhook_url}",
                "-d", dd_payload,
                "https://api.datadoghq.com/api/v1/events",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and '"ok"' in result.stdout

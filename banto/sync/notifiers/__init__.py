# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Notification integrations for banto sync events."""
from __future__ import annotations

from .base import Notifier, SyncEvent
from .datadog import DatadogNotifier
from .pagerduty import PagerDutyNotifier
from .slack import SlackNotifier
from .teams import TeamsNotifier

NOTIFIER_MAP: dict[str, type[Notifier]] = {
    "slack": SlackNotifier,
    "teams": TeamsNotifier,
    "datadog": DatadogNotifier,
    "pagerduty": PagerDutyNotifier,
}


def get_notifier(name: str, webhook_url: str) -> Notifier:
    """Return a notifier instance."""
    cls = NOTIFIER_MAP.get(name)
    if cls is None:
        supported = ", ".join(sorted(NOTIFIER_MAP.keys())) if NOTIFIER_MAP else "(none registered)"
        raise ValueError(f"Unknown notifier: {name} (supported: {supported})")
    return cls(webhook_url=webhook_url)


__all__ = [
    "Notifier",
    "SyncEvent",
    "NOTIFIER_MAP",
    "get_notifier",
]

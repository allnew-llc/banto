# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""
profiles.py - Model profile management for banto.

Profiles map task roles (chat, verify, embed) to specific models,
enabling easy switching between quality tiers without changing
application code.

Three built-in profiles:
    quality  - Premium models for best results
    balanced - Mix of quality and cost efficiency (default)
    budget   - Prioritize cost savings
"""

import re

DEFAULT_PROFILES: dict[str, dict[str, str]] = {
    "quality": {
        "chat": "claude-opus-4-6",
        "verify": "claude-sonnet-4-6",
        "embed": "text-embedding-3-large",
    },
    "balanced": {
        "chat": "claude-sonnet-4-6",
        "verify": "claude-haiku-4-5",
        "embed": "text-embedding-3-small",
    },
    "budget": {
        "chat": "claude-haiku-4-5",
        "verify": "claude-haiku-4-5",
        "embed": "text-embedding-3-small",
    },
}

DEFAULT_ACTIVE_PROFILE = "balanced"

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PROFILE_NAME_MAX_LEN = 64


def _validate_profile_name(name: str) -> None:
    """Validate a profile name.

    Raises:
        ValueError: If name is not a non-empty string of alphanumeric,
                    hyphen, or underscore characters (max 64 chars).
    """
    if not isinstance(name, str) or not name:
        raise ValueError("Profile name must be a non-empty string")
    if len(name) > _PROFILE_NAME_MAX_LEN:
        raise ValueError(
            f"Profile name too long ({len(name)} chars, max {_PROFILE_NAME_MAX_LEN})"
        )
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid profile name: {name!r}. "
            "Only alphanumeric characters, hyphens, and underscores are allowed."
        )


class ProfileManager:
    """Manages named model profiles for role-based model resolution.

    Each profile maps task roles (e.g. "chat", "verify", "embed") to
    specific model names. The active profile determines which models
    are used when resolving by role.

    Usage:
        pm = ProfileManager(config)
        model = pm.resolve_model("chat")  # -> model name from active profile
        pm.active_profile = "budget"      # switch profile
    """

    def __init__(self, config: dict):
        """
        Args:
            config: Configuration dict. Reads "model_profiles" and
                    "active_profile" keys. Falls back to defaults
                    if not present.
        """
        raw_profiles = config.get("model_profiles", DEFAULT_PROFILES)
        # Validate all profile names from config
        self._profiles: dict[str, dict[str, str]] = {}
        for name, models in raw_profiles.items():
            _validate_profile_name(name)
            self._profiles[name] = models

        active = config.get("active_profile", DEFAULT_ACTIVE_PROFILE)
        if isinstance(active, str) and active:
            _validate_profile_name(active)
        self._active: str = active

        # Validate that active profile exists in profiles
        if self._active not in self._profiles:
            self._active = DEFAULT_ACTIVE_PROFILE

    @property
    def active_profile(self) -> str:
        """Name of the currently active profile."""
        return self._active

    @active_profile.setter
    def active_profile(self, name: str) -> None:
        """Switch the active profile.

        Raises:
            ValueError: If name is not a known profile or invalid format.
        """
        _validate_profile_name(name)
        if name not in self._profiles:
            raise ValueError(
                f"Unknown profile: {name}. "
                f"Available: {list(self._profiles.keys())}"
            )
        self._active = name

    def resolve_model(self, role: str) -> str:
        """Resolve a task role to a model name using the active profile.

        Args:
            role: Task role (e.g. "chat", "verify", "embed").

        Returns:
            Model name string.

        Raises:
            ValueError: If the role is not defined in the active profile.
        """
        profile = self._profiles.get(self._active, {})
        if role not in profile:
            raise ValueError(
                f"Unknown role '{role}' in profile '{self._active}'. "
                f"Available: {list(profile.keys())}"
            )
        return profile[role]

    def list_profiles(self) -> dict[str, dict]:
        """Return all profiles with active indicator.

        Returns:
            Dict mapping profile name to {"models": {...}, "active": bool}.
        """
        return {
            name: {"models": models, "active": name == self._active}
            for name, models in self._profiles.items()
        }

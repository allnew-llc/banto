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
        self._profiles: dict[str, dict[str, str]] = config.get(
            "model_profiles", DEFAULT_PROFILES
        )
        self._active: str = config.get("active_profile", DEFAULT_ACTIVE_PROFILE)

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
            ValueError: If name is not a known profile.
        """
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

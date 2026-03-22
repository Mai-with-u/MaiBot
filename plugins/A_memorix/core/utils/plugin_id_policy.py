"""Plugin ID matching policy for A_Memorix."""

from __future__ import annotations

from typing import Any


class PluginIdPolicy:
    """Centralized plugin id normalization/matching policy."""

    CANONICAL_ID = "a_memorix"

    @classmethod
    def normalize(cls, plugin_id: Any) -> str:
        if not isinstance(plugin_id, str):
            return ""
        return plugin_id.strip().lower()

    @classmethod
    def is_target_plugin_id(cls, plugin_id: Any) -> bool:
        normalized = cls.normalize(plugin_id)
        if not normalized:
            return False
        if normalized == cls.CANONICAL_ID:
            return True
        return normalized.split(".")[-1] == cls.CANONICAL_ID


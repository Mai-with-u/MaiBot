"""A_Memorix 插件 ID 匹配策略。"""

from __future__ import annotations

from typing import Any


class PluginIdPolicy:
    """集中管理插件 ID 的归一化与匹配规则。"""

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

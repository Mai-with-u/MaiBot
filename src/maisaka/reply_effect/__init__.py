"""Maisaka 回复效果观察器。"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tracker import ReplyEffectTracker

__all__ = ["ReplyEffectTracker"]


def __getattr__(name: str) -> Any:
    """仅在运行时需要追踪器时加载，避免读取数据模型触发完整聊天依赖。"""

    if name == "ReplyEffectTracker":
        from .tracker import ReplyEffectTracker

        return ReplyEffectTracker
    raise AttributeError(name)

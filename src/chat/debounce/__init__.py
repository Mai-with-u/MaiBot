"""按固定规则合并会话中的连发消息，减少重复判断。"""

from .controller import MessageDebouncer

__all__ = ["MessageDebouncer"]

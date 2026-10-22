"""Maisaka 引用决策：共享消息计数器与群聊、私聊固定规则。"""

from .counter import ReplyQuoteContext, ReplyQuoteCounter
from .rules import decide_quote

__all__ = ["ReplyQuoteContext", "ReplyQuoteCounter", "decide_quote"]

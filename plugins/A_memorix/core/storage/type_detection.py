"""Heuristic detection for import strategies and stored knowledge types."""

from __future__ import annotations

import re
from typing import Optional

from .knowledge_types import (
    ImportStrategy,
    KnowledgeType,
    parse_import_strategy,
    resolve_stored_knowledge_type,
)


_NARRATIVE_MARKERS = [
    r"然后",
    r"接着",
    r"于是",
    r"后来",
    r"最后",
    r"突然",
    r"一天",
    r"曾经",
    r"有一次",
    r"从前",
    r"说道",
    r"问道",
    r"想着",
    r"觉得",
]
_FACTUAL_MARKERS = [
    r"是",
    r"有",
    r"在",
    r"为",
    r"属于",
    r"位于",
    r"包含",
    r"拥有",
    r"成立于",
    r"出生于",
]


def _non_empty_lines(content: str) -> list[str]:
    return [line for line in str(content or "").splitlines() if line.strip()]


def looks_like_structured_text(content: str) -> bool:
    text = str(content or "").strip()
    if "|" not in text or text.count("|") < 2:
        return False
    parts = text.split("|")
    return len(parts) == 3 and all(part.strip() for part in parts)


def looks_like_quote_text(content: str) -> bool:
    lines = _non_empty_lines(content)
    if len(lines) < 5:
        return False
    avg_len = sum(len(line) for line in lines) / len(lines)
    return avg_len < 20


def looks_like_narrative_text(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False

    narrative_score = sum(1 for marker in _NARRATIVE_MARKERS if re.search(marker, text))
    has_dialogue = bool(re.search(r'["「『].*?["」』]', text))
    has_chapter = any(token in text[:500] for token in ("Chapter", "CHAPTER", "###"))
    return has_chapter or has_dialogue or narrative_score >= 2


def looks_like_factual_text(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if looks_like_structured_text(text) or looks_like_quote_text(text):
        return False

    factual_score = sum(1 for marker in _FACTUAL_MARKERS if re.search(r"\s*" + marker + r"\s*", text))
    if factual_score <= 0:
        return False

    if len(text) <= 240:
        return True
    return factual_score >= 2 and not looks_like_narrative_text(text)


def select_import_strategy(
    content: str,
    *,
    override: Optional[str | ImportStrategy] = None,
    chat_log: bool = False,
) -> ImportStrategy:
    """文本导入策略选择：override > quote > factual > narrative。"""

    if chat_log:
        return ImportStrategy.NARRATIVE

    strategy = parse_import_strategy(override, default=ImportStrategy.AUTO)
    if strategy != ImportStrategy.AUTO:
        return strategy

    if looks_like_quote_text(content):
        return ImportStrategy.QUOTE
    if looks_like_factual_text(content):
        return ImportStrategy.FACTUAL
    return ImportStrategy.NARRATIVE


def detect_knowledge_type(content: str) -> KnowledgeType:
    """自动检测落库 knowledge_type；无法可靠判断时回退 mixed。"""

    text = str(content or "").strip()
    if not text:
        return KnowledgeType.MIXED
    if looks_like_structured_text(text):
        return KnowledgeType.STRUCTURED
    if looks_like_quote_text(text):
        return KnowledgeType.QUOTE
    if looks_like_factual_text(text):
        return KnowledgeType.FACTUAL
    if looks_like_narrative_text(text):
        return KnowledgeType.NARRATIVE
    return KnowledgeType.MIXED


def get_type_from_user_input(type_hint: Optional[str], content: str) -> KnowledgeType:
    """优先使用显式 type_hint，否则自动检测。"""

    if type_hint:
        return resolve_stored_knowledge_type(type_hint, content=content)
    return detect_knowledge_type(content)

"""Knowledge type and import strategy helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class KnowledgeType(str, Enum):
    """持久化到 paragraphs.knowledge_type 的合法类型。"""

    STRUCTURED = "structured"
    NARRATIVE = "narrative"
    FACTUAL = "factual"
    QUOTE = "quote"
    MIXED = "mixed"


class ImportStrategy(str, Enum):
    """文本导入阶段的策略选择。"""

    AUTO = "auto"
    NARRATIVE = "narrative"
    FACTUAL = "factual"
    QUOTE = "quote"


def allowed_knowledge_type_values() -> tuple[str, ...]:
    return tuple(item.value for item in KnowledgeType)


def allowed_import_strategy_values() -> tuple[str, ...]:
    return tuple(item.value for item in ImportStrategy)


def get_knowledge_type_from_string(type_str: Any) -> Optional[KnowledgeType]:
    """从字符串解析合法的落库知识类型。"""

    if not isinstance(type_str, str):
        return None
    normalized = type_str.lower().strip()
    try:
        return KnowledgeType(normalized)
    except ValueError:
        return None


def get_import_strategy_from_string(value: Any) -> Optional[ImportStrategy]:
    """从字符串解析文本导入策略。"""

    if not isinstance(value, str):
        return None
    normalized = value.lower().strip()
    try:
        return ImportStrategy(normalized)
    except ValueError:
        return None


def parse_import_strategy(value: Any, default: ImportStrategy = ImportStrategy.AUTO) -> ImportStrategy:
    """解析 import strategy；非法值直接报错。"""

    if value is None:
        return default
    if isinstance(value, ImportStrategy):
        return value

    normalized = str(value or "").strip().lower()
    if not normalized:
        return default

    strategy = get_import_strategy_from_string(normalized)
    if strategy is None:
        allowed = "/".join(allowed_import_strategy_values())
        raise ValueError(f"strategy_override 必须为 {allowed}")
    return strategy


def validate_stored_knowledge_type(value: Any) -> KnowledgeType:
    """校验写库 knowledge_type，仅允许合法落库类型。"""

    if isinstance(value, KnowledgeType):
        return value

    resolved = get_knowledge_type_from_string(value)
    if resolved is None:
        allowed = "/".join(allowed_knowledge_type_values())
        raise ValueError(f"knowledge_type 必须为 {allowed}")
    return resolved


def resolve_stored_knowledge_type(
    value: Any,
    *,
    content: str = "",
    allow_legacy: bool = False,
    unknown_fallback: Optional[KnowledgeType] = None,
) -> KnowledgeType:
    """
    将策略/字符串/旧值解析为合法落库类型。

    `allow_legacy=True` 仅供迁移使用。
    """

    if isinstance(value, KnowledgeType):
        return value

    if isinstance(value, ImportStrategy):
        if value == ImportStrategy.AUTO:
            if not str(content or "").strip():
                raise ValueError("knowledge_type=auto 需要 content 才能推断")
            from .type_detection import detect_knowledge_type

            return detect_knowledge_type(content)
        return KnowledgeType(value.value)

    raw = str(value or "").strip()
    if not raw:
        if str(content or "").strip():
            from .type_detection import detect_knowledge_type

            return detect_knowledge_type(content)
        raise ValueError("knowledge_type 不能为空")

    direct = get_knowledge_type_from_string(raw)
    if direct is not None:
        return direct

    strategy = get_import_strategy_from_string(raw)
    if strategy is not None:
        return resolve_stored_knowledge_type(strategy, content=content)

    if allow_legacy:
        normalized = raw.lower()
        if normalized == "imported":
            return KnowledgeType.FACTUAL
        if str(content or "").strip():
            from .type_detection import detect_knowledge_type

            detected = detect_knowledge_type(content)
            if detected is not None:
                return detected
        if unknown_fallback is not None:
            return unknown_fallback

    allowed = "/".join(allowed_knowledge_type_values())
    raise ValueError(f"非法 knowledge_type: {raw}（仅允许 {allowed}）")


def should_extract_relations(knowledge_type: KnowledgeType) -> bool:
    """判断是否应该做关系抽取。"""

    return knowledge_type in [
        KnowledgeType.STRUCTURED,
        KnowledgeType.FACTUAL,
        KnowledgeType.MIXED,
    ]


def get_default_chunk_size(knowledge_type: KnowledgeType) -> int:
    """获取默认分块大小。"""

    chunk_sizes = {
        KnowledgeType.STRUCTURED: 300,
        KnowledgeType.NARRATIVE: 800,
        KnowledgeType.FACTUAL: 500,
        KnowledgeType.QUOTE: 400,
        KnowledgeType.MIXED: 500,
    }
    return chunk_sizes.get(knowledge_type, 500)


def get_type_display_name(knowledge_type: KnowledgeType) -> str:
    """获取知识类型中文名称。"""

    display_names = {
        KnowledgeType.STRUCTURED: "结构化知识",
        KnowledgeType.NARRATIVE: "叙事性文本",
        KnowledgeType.FACTUAL: "事实陈述",
        KnowledgeType.QUOTE: "引用文本",
        KnowledgeType.MIXED: "混合类型",
    }
    return display_names.get(knowledge_type, "未知类型")

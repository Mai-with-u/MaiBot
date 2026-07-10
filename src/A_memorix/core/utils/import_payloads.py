"""导入载荷共用的归一化辅助工具。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import re

from ..storage import KnowledgeType, resolve_stored_knowledge_type
from .time_parser import normalize_time_meta

_HASH_TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]+$")
_ENTITY_NAME_KEYS = ("name", "label", "entity")


class ImportPayloadValidationError(ValueError):
    """导入负载校验异常（可用于上层按项跳过并记录告警）。"""

    def __init__(self, message: str, *, code: str, field: str = "", value: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.value = value


def is_probable_hash_token(value: Any) -> bool:
    """判断文本是否疑似哈希值（hex 串，长度为 32/40/64）。"""

    text = str(value or "").strip()
    if len(text) not in {32, 40, 64}:
        return False
    return bool(_HASH_TOKEN_PATTERN.fullmatch(text))


def normalize_entity_import_item(item: Any) -> Optional[str]:
    """标准化实体导入项。

    支持：
    - 字符串实体名
    - 对象实体（提取 name/label/entity 字段）
    """

    if isinstance(item, str):
        name = item.strip()
    elif isinstance(item, dict):
        name = ""
        for key in _ENTITY_NAME_KEYS:
            candidate = str(item.get(key, "") or "").strip()
            if candidate:
                name = candidate
                break
    else:
        name = ""

    if not name or is_probable_hash_token(name):
        return None
    return name


def normalize_relation_import_item(item: Any) -> Optional[Dict[str, str]]:
    """标准化关系导入项。"""

    if not isinstance(item, dict):
        return None

    subject = str(item.get("subject", "") or "").strip()
    predicate = str(item.get("predicate", "") or "").strip()
    obj = str(item.get("object", "") or "").strip()
    if not (subject and predicate and obj):
        return None
    if any(is_probable_hash_token(token) for token in (subject, predicate, obj)):
        return None
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
    }


def _normalize_entities(raw_entities: Any) -> List[str]:
    if not isinstance(raw_entities, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw_entities:
        name = normalize_entity_import_item(item)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _normalize_relations(raw_relations: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_relations, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw_relations:
        relation = normalize_relation_import_item(item)
        if relation is None:
            continue
        out.append(relation)
    return out


def normalize_paragraph_import_item(
    item: Any,
    *,
    default_source: str,
) -> Dict[str, Any]:
    """归一化来自文本或 JSON 载荷的一条段落导入项。"""

    if isinstance(item, str):
        content = str(item or "")
        if not content.strip():
            raise ImportPayloadValidationError(
                "段落 content 不能为空",
                code="paragraph_content_empty",
                field="content",
            )
        if is_probable_hash_token(content):
            raise ImportPayloadValidationError(
                "段落 content 疑似哈希值，已跳过",
                code="paragraph_content_hash_like",
                field="content",
                value=content,
            )
        knowledge_type = resolve_stored_knowledge_type(None, content=content)
        return {
            "content": content,
            "knowledge_type": knowledge_type.value,
            "source": str(default_source or "").strip(),
            "time_meta": None,
            "entities": [],
            "relations": [],
        }

    if not isinstance(item, dict) or "content" not in item:
        raise ImportPayloadValidationError(
            "段落项必须为字符串或包含 content 的对象",
            code="paragraph_item_invalid",
            field="content",
        )

    content = str(item.get("content", "") or "")
    if not content.strip():
        raise ImportPayloadValidationError(
            "段落 content 不能为空",
            code="paragraph_content_empty",
            field="content",
        )
    if is_probable_hash_token(content):
        raise ImportPayloadValidationError(
            "段落 content 疑似哈希值，已跳过",
            code="paragraph_content_hash_like",
            field="content",
            value=content,
        )

    raw_time_meta = {
        "event_time": item.get("event_time"),
        "event_time_start": item.get("event_time_start"),
        "event_time_end": item.get("event_time_end"),
        "time_range": item.get("time_range"),
        "time_granularity": item.get("time_granularity"),
        "time_confidence": item.get("time_confidence"),
    }
    time_meta_field = item.get("time_meta")
    if isinstance(time_meta_field, dict):
        raw_time_meta.update(time_meta_field)

    knowledge_type_raw = item.get("knowledge_type")
    if knowledge_type_raw is None:
        knowledge_type_raw = item.get("type")
    knowledge_type = resolve_stored_knowledge_type(knowledge_type_raw, content=content)
    source = str(item.get("source") or default_source or "").strip()
    if not source:
        source = str(default_source or "").strip()

    normalized_time_meta = normalize_time_meta(raw_time_meta)
    return {
        "content": content,
        "knowledge_type": knowledge_type.value,
        "source": source,
        "time_meta": normalized_time_meta if normalized_time_meta else None,
        "entities": _normalize_entities(item.get("entities")),
        "relations": _normalize_relations(item.get("relations")),
    }


def normalize_summary_knowledge_type(value: Any) -> KnowledgeType:
    """归一化由配置指定的摘要知识类型。"""

    return resolve_stored_knowledge_type(value, content="")

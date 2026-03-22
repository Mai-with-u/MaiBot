"""Shared import payload normalization helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..storage import KnowledgeType, resolve_stored_knowledge_type
from .time_parser import normalize_time_meta


def _normalize_entities(raw_entities: Any) -> List[str]:
    if not isinstance(raw_entities, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw_entities:
        name = str(item or "").strip()
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
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        if not (subject and predicate and obj):
            continue
        out.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            }
        )
    return out


def normalize_paragraph_import_item(
    item: Any,
    *,
    default_source: str,
) -> Dict[str, Any]:
    """Normalize one paragraph import item from text/json payloads."""

    if isinstance(item, str):
        content = str(item)
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
        raise ValueError("段落项必须为字符串或包含 content 的对象")

    content = str(item.get("content", "") or "")
    if not content.strip():
        raise ValueError("段落 content 不能为空")

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
    """Normalize config-driven summary knowledge type."""

    return resolve_stored_knowledge_type(value, content="")

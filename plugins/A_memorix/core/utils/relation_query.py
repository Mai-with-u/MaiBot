"""关系查询规格解析工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class RelationQuerySpec:
    raw: str
    is_structured: bool
    subject: Optional[str]
    predicate: Optional[str]
    object: Optional[str]
    error: Optional[str] = None


_NATURAL_LANGUAGE_PATTERN = re.compile(
    r"(^\s*(what|who|which|how|why|when|where)\b|"
    r"\?|？|"
    r"\b(relation|related|between)\b|"
    r"(什么关系|有哪些关系|之间|关联))",
    re.IGNORECASE,
)


def _looks_like_natural_language(raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    return _NATURAL_LANGUAGE_PATTERN.search(text) is not None


def parse_relation_query_spec(relation_spec: str) -> RelationQuerySpec:
    raw = str(relation_spec or "").strip()
    if not raw:
        return RelationQuerySpec(
            raw=raw,
            is_structured=False,
            subject=None,
            predicate=None,
            object=None,
            error="empty",
        )

    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 2:
            return RelationQuerySpec(
                raw=raw,
                is_structured=True,
                subject=None,
                predicate=None,
                object=None,
                error="invalid_pipe_format",
            )
        return RelationQuerySpec(
            raw=raw,
            is_structured=True,
            subject=parts[0] or None,
            predicate=parts[1] or None,
            object=parts[2] if len(parts) > 2 and parts[2] else None,
        )

    if "->" in raw:
        parts = [p.strip() for p in raw.split("->") if p.strip()]
        if len(parts) >= 3:
            return RelationQuerySpec(
                raw=raw,
                is_structured=True,
                subject=parts[0],
                predicate=parts[1],
                object=parts[2],
            )
        if len(parts) == 2:
            return RelationQuerySpec(
                raw=raw,
                is_structured=True,
                subject=parts[0],
                predicate=None,
                object=parts[1],
            )
        return RelationQuerySpec(
            raw=raw,
            is_structured=True,
            subject=None,
            predicate=None,
            object=None,
            error="invalid_arrow_format",
        )

    if _looks_like_natural_language(raw):
        return RelationQuerySpec(
            raw=raw,
            is_structured=False,
            subject=None,
            predicate=None,
            object=None,
        )

    # 仅保留低歧义的紧凑三元组作为兼容语法，例如 "Alice likes Apple"。
    # 两词形式过于模糊，不再视为结构化关系查询。
    parts = raw.split()
    if len(parts) == 3:
        return RelationQuerySpec(
            raw=raw,
            is_structured=True,
            subject=parts[0],
            predicate=parts[1],
            object=parts[2],
        )

    return RelationQuerySpec(
        raw=raw,
        is_structured=False,
        subject=None,
        predicate=None,
        object=None,
    )

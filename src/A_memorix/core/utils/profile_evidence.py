from __future__ import annotations

from typing import Any, Dict, Optional


def profile_evidence_type_from_source(source: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    source_type = str(meta.get("source_type", "") or "").strip()
    if source_type in {"person_fact", "chat_summary"}:
        return source_type
    token = str(source or meta.get("source", "") or "").strip()
    if token.startswith("person_fact:"):
        return "person_fact"
    if token.startswith("chat_summary:"):
        return "chat_summary"
    return "paragraph"


def profile_relation_content(relation: Dict[str, Any]) -> str:
    subject = str(relation.get("subject", "") or "").strip()
    predicate = str(relation.get("predicate", "") or "").strip()
    obj = str(relation.get("object", "") or "").strip()
    if subject and predicate and obj:
        return f"{subject} -[{predicate}]-> {obj}"
    return " ".join(item for item in (subject, predicate, obj) if item).strip()

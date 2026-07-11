from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from json_repair import repair_json
import json


def coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def safe_json_loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        repaired = repair_json(text)
        payload = json.loads(repaired) if isinstance(repaired, str) else repaired
    except Exception:
        payload = None
    return payload if isinstance(payload, dict) else {}


def tokens(values: Optional[Iterable[Any]]) -> List[str]:
    if isinstance(values, str):
        values = [values]
    result: List[str] = []
    seen = set()
    for item in values or []:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def merge_tokens(*groups: Optional[Iterable[Any]]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for item in tokens(group):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def argument_tokens(value: Any) -> List[str]:
    if isinstance(value, str):
        return tokens([value])
    return tokens(value)


def merge_argument_tokens(*groups: Any) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for item in argument_tokens(group):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def build_source(source_type: str, chat_id: str, person_ids: Sequence[str]) -> str:
    clean_type = str(source_type or "").strip() or "memory"
    if clean_type == "chat_summary" and chat_id:
        return f"chat_summary:{chat_id}"
    if clean_type == "person_fact" and person_ids:
        return f"person_fact:{person_ids[0]}"
    return f"{clean_type}:{chat_id}" if chat_id else clean_type


def resolve_knowledge_type(source_type: str) -> str:
    clean_type = str(source_type or "").strip().lower()
    if clean_type == "person_fact":
        return "factual"
    if clean_type == "chat_summary":
        return "narrative"
    return "mixed"


def time_meta(timestamp: Optional[float], time_start: Optional[float], time_end: Optional[float]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if timestamp is not None:
        payload["event_time"] = float(timestamp)
    if time_start is not None:
        payload["event_time_start"] = float(time_start)
    if time_end is not None:
        payload["event_time_end"] = float(time_end)
    if payload:
        payload["time_granularity"] = "minute"
        payload["time_confidence"] = 0.95
    return payload


def optional_float(value: Any) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except Exception:
        return None


def optional_int(value: Any) -> Optional[int]:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except Exception:
        return None

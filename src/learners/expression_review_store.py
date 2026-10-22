from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import uuid

from src.common.logger import get_logger
from src.manager.local_store_manager import local_storage

logger = get_logger("expressor")

REVIEW_LOG_PATH = Path("logs/expression_review/review_logs.json")
AI_REVIEW_EVENT = "ai_review"
MANUAL_RESCUE_EVENT = "manual_rescue"


def _review_key(expression_id: int) -> str:
    return f"expression_review:{expression_id}"


def get_review_state(expression_id: Optional[int]) -> Dict[str, Any]:
    if expression_id is None:
        return {"checked": False, "rejected": False, "modified_by": None}
    value = local_storage[_review_key(expression_id)]
    if isinstance(value, dict):
        return {
            "checked": bool(value.get("checked", False)),
            "rejected": bool(value.get("rejected", False)),
            "modified_by": value.get("modified_by"),
        }
    return {"checked": False, "rejected": False, "modified_by": None}


def set_review_state(
    expression_id: Optional[int],
    checked: bool,
    rejected: bool,
    modified_by: Optional[str],
) -> None:
    if expression_id is None:
        return
    local_storage[_review_key(expression_id)] = {
        "checked": checked,
        "rejected": rejected,
        "modified_by": modified_by,
    }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_log_entry(entry: Dict[str, Any]) -> None:
    entries = read_review_log_entries()
    entries.append(entry)
    REVIEW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_LOG_PATH.open("w", encoding="utf-8") as log_file:
        json.dump(entries, log_file, ensure_ascii=False, indent=2)


def append_ai_review_log(
    *,
    session_id: str,
    situation: str,
    style: str,
    passed: bool,
    reason: str,
    source: str,
    expression_id: Optional[int] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """记录一次表达方式 AI 审核结果，供 WebUI 回看和人工救回。"""

    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "event": AI_REVIEW_EVENT,
        "created_at": _now_iso(),
        "expression_id": expression_id,
        "session_id": str(session_id or "").strip(),
        "passed": bool(passed),
        "reason": str(reason or "").strip(),
        "situation": str(situation or "").strip(),
        "style": str(style or "").strip(),
        "source": source,
    }
    if error:
        entry["error"] = str(error)

    _append_log_entry(entry)
    logger.debug(
        "表达方式优化记录已写入 "
        f"{REVIEW_LOG_PATH.as_posix()}: passed={entry['passed']} "
        f"session_id={entry['session_id']} reason={entry['reason']}"
    )
    return entry


def append_manual_rescue_log(
    *,
    review_log_id: str,
    expression_id: int,
) -> Dict[str, Any]:
    """记录一次从 AI 审核日志中人工恢复表达方式的操作。"""

    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "event": MANUAL_RESCUE_EVENT,
        "created_at": _now_iso(),
        "review_log_id": str(review_log_id),
        "expression_id": int(expression_id),
    }
    _append_log_entry(entry)
    return entry


def read_review_log_entries() -> List[Dict[str, Any]]:
    """读取全部表达方式审核日志；文件损坏时删除重建，继续本次学习。"""

    if not REVIEW_LOG_PATH.exists():
        return []

    try:
        with REVIEW_LOG_PATH.open("r", encoding="utf-8") as log_file:
            parsed = json.load(log_file)
        if not isinstance(parsed, list):
            raise ValueError("表达方式审核日志格式异常，应为 JSON 数组")
    except ValueError as exc:
        # 审核历史损坏不应阻断当前表达入库，清空后由调用方继续记录本次审核。
        REVIEW_LOG_PATH.unlink(missing_ok=True)
        REVIEW_LOG_PATH.write_text("[]", encoding="utf-8")
        logger.warning(f"表达方式审核日志损坏，已删除并重建 {REVIEW_LOG_PATH.as_posix()}: {exc}")
        return []

    return [entry for entry in parsed if isinstance(entry, dict)]


def _parse_created_at(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(0)


def _rescue_by_review_id(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    rescue_entries = [
        entry
        for entry in entries
        if entry.get("event") == MANUAL_RESCUE_EVENT and str(entry.get("review_log_id") or "").strip()
    ]
    rescue_entries.sort(key=lambda entry: _parse_created_at(entry.get("created_at")), reverse=True)
    rescues: Dict[str, Dict[str, Any]] = {}
    for rescue_entry in rescue_entries:
        review_log_id = str(rescue_entry.get("review_log_id") or "").strip()
        rescues.setdefault(review_log_id, rescue_entry)
    return rescues


def _with_rescue_state(entry: Dict[str, Any], rescue_entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    enriched_entry = dict(entry)
    enriched_entry["rescued"] = rescue_entry is not None
    enriched_entry["rescued_expression_id"] = rescue_entry.get("expression_id") if rescue_entry else None
    enriched_entry["rescued_at"] = rescue_entry.get("created_at") if rescue_entry else None
    return enriched_entry


def get_recent_ai_review_logs(
    *,
    limit: int = 50,
    passed: Optional[bool] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """返回最近的表达方式 AI 审核记录。"""

    entries = read_review_log_entries()
    rescues = _rescue_by_review_id(entries)
    normalized_session_id = str(session_id or "").strip()
    review_entries = [
        entry
        for entry in entries
        if entry.get("event", AI_REVIEW_EVENT) == AI_REVIEW_EVENT and str(entry.get("id") or "").strip()
    ]

    if passed is not None:
        review_entries = [entry for entry in review_entries if bool(entry.get("passed")) is passed]

    if normalized_session_id:
        review_entries = [
            entry
            for entry in review_entries
            if str(entry.get("session_id") or "").strip() == normalized_session_id
        ]

    review_entries.sort(key=lambda entry: _parse_created_at(entry.get("created_at")), reverse=True)
    normalized_limit = max(1, int(limit))
    return [
        _with_rescue_state(entry, rescues.get(str(entry.get("id"))))
        for entry in review_entries[:normalized_limit]
    ]


def get_ai_review_log(review_log_id: str) -> Optional[Dict[str, Any]]:
    """按日志 ID 获取一条表达方式 AI 审核记录。"""

    normalized_id = str(review_log_id or "").strip()
    if not normalized_id:
        return None

    entries = read_review_log_entries()
    rescues = _rescue_by_review_id(entries)
    for entry in entries:
        if entry.get("event", AI_REVIEW_EVENT) != AI_REVIEW_EVENT:
            continue
        if str(entry.get("id") or "").strip() == normalized_id:
            return _with_rescue_state(entry, rescues.get(normalized_id))
    return None

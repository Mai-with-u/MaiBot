import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class PlanReplyLogger:
    """独立的Plan/Reply日志记录器，负责落盘和容量控制。"""

    _BASE_DIR = Path("logs")
    _PLAN_DIR = _BASE_DIR / "plan"
    _REPLY_DIR = _BASE_DIR / "reply"
    _MAX_PER_CHAT = 1000
    _TRIM_COUNT = 100

    @classmethod
    def log_plan(
        cls,
        chat_id: str,
        prompt: str,
        reasoning: str,
        raw_output: Optional[str],
        raw_reasoning: Optional[str],
        actions: List[Any],
        timing: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "type": "plan",
            "chat_id": chat_id,
            "timestamp": time.time(),
            "prompt": prompt,
            "reasoning": reasoning,
            "raw_output": raw_output,
            "raw_reasoning": raw_reasoning,
            "actions": [cls._serialize_action(action) for action in actions],
            "timing": timing or {},
            "extra": cls._safe_data(extra),
        }
        cls._write_json(cls._PLAN_DIR, chat_id, payload)

    @classmethod
    def log_reply(
        cls,
        chat_id: str,
        prompt: str,
        output: Optional[str],
        processed_output: Optional[List[Any]],
        model: Optional[str],
        timing: Optional[Dict[str, Any]] = None,
        reasoning: Optional[str] = None,
        think_level: Optional[int] = None,
        error: Optional[str] = None,
        success: bool = True,
    ) -> None:
        payload = {
            "type": "reply",
            "chat_id": chat_id,
            "timestamp": time.time(),
            "prompt": prompt,
            "output": output,
            "processed_output": cls._safe_data(processed_output),
            "model": model,
            "reasoning": reasoning,
            "think_level": think_level,
            "timing": timing or {},
            "error": error if not success else None,
            "success": success,
        }
        cls._write_json(cls._REPLY_DIR, chat_id, payload)

    @classmethod
    def _write_json(cls, base_dir: Path, chat_id: str, payload: Dict[str, Any]) -> None:
        chat_dir = base_dir / chat_id
        chat_dir.mkdir(parents=True, exist_ok=True)
        file_path = chat_dir / f"{int(time.time() * 1000)}_{uuid4().hex[:8]}.json"
        try:
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(cls._safe_data(payload), f, ensure_ascii=False, indent=2)
        finally:
            cls._trim_overflow(chat_dir)

    @classmethod
    def _trim_overflow(cls, chat_dir: Path) -> None:
        """超过阈值时删除最老的若干文件，避免目录无限增长。"""
        files = sorted(chat_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if len(files) <= cls._MAX_PER_CHAT:
            return
        # 删除最老的 TRIM_COUNT 条
        for old_file in files[: cls._TRIM_COUNT]:
            try:
                old_file.unlink()
            except FileNotFoundError:
                continue

    @classmethod
    def _serialize_action(cls, action: Any) -> Dict[str, Any]:
        # ActionPlannerInfo 结构的轻量序列化，避免引用复杂对象
        message_info = None
        action_message = getattr(action, "action_message", None)
        if action_message:
            user_info = getattr(action_message, "user_info", None)
            message_info = {
                "message_id": getattr(action_message, "message_id", None),
                "user_id": getattr(user_info, "user_id", None) if user_info else None,
                "platform": getattr(user_info, "platform", None) if user_info else None,
                "text": getattr(action_message, "processed_plain_text", None),
            }

        return {
            "action_type": getattr(action, "action_type", None),
            "reasoning": getattr(action, "reasoning", None),
            "action_data": cls._safe_data(getattr(action, "action_data", None)),
            "action_message": message_info,
            "available_actions": cls._safe_data(getattr(action, "available_actions", None)),
            "action_reasoning": getattr(action, "action_reasoning", None),
        }

    @classmethod
    def _safe_data(cls, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k): cls._safe_data(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._safe_data(v) for v in value]
        if isinstance(value, Path):
            return str(value)
        # Fallback to string for other complex types
        return str(value)


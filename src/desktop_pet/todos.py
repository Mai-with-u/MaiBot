"""桌宠窗口线程与 Agent 指令共用的待办存储，完成即删除。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import math
import threading
import time
import uuid


class TodoStore:
    """待办列表，完成即删除。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._items: List[Dict[str, Any]] = json.loads(path.read_text("utf-8")) if path.exists() else []
        for item in self._items:
            item.setdefault("condition_type", "natural")
            item.setdefault("condition", "")
            item.setdefault("kind", "user")
            item.setdefault("due_at", None)
            item.setdefault("status", "pending")
            item.setdefault("revision", uuid.uuid4().hex)
            if item["status"] == "dispatching":
                item["status"] = "pending"

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def add(self, text: str, **options: Any) -> Dict[str, Any]:
        text = self._validate_text(text)
        item = {"id": uuid.uuid4().hex, "text": text, "created_at": time.time(), "kind": "user", "due_at": None,
                "status": "pending", "condition": "", "condition_type": "natural", "revision": uuid.uuid4().hex}
        item = self._apply_options(item, options)
        with self._lock:
            self._save([*self._items, item])
        return dict(item)

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError("待办内容必须为 1 至 200 字")
        return text.strip()

    def update(self, todo_id: str, text: Optional[str] = None, **options: Any) -> Dict[str, Any]:
        if text is not None:
            text = self._validate_text(text)
        with self._lock:
            item = next((item for item in self._items if item["id"] == todo_id), None)
            if item is None:
                raise ValueError("待办不存在，请重新查询列表")
            updated = self._apply_options({**item, "text": item["text"] if text is None else text}, options)
            if any(updated[key] != item[key] for key in ("text", "kind", "due_at", "condition", "condition_type")):
                updated["updated_at"] = time.time()
            updated["revision"] = uuid.uuid4().hex
            if updated["status"] == "dispatching":
                updated["status"] = "pending"
            self._save([updated if entry["id"] == todo_id else entry for entry in self._items])
            return dict(updated)

    @staticmethod
    def _apply_options(item: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
        if set(options) - {"kind", "due_at", "delay_seconds", "condition", "condition_type"}:
            raise ValueError("未知待办字段")
        if "condition_type" in options:
            kind = options["condition_type"]
            if kind not in {"natural", "app"}:
                raise ValueError("条件类型必须为 natural 或 app")
            if kind != item["condition_type"]:
                item.update(condition_type=kind, status="pending", error="", evidence="")
        if "condition" in options:
            condition = options["condition"]
            if not isinstance(condition, str) or len(condition.strip()) > 500:
                raise ValueError("触发条件必须是最多 500 字的文本，空字符串取消条件")
            if condition.strip() != item["condition"]:
                item.update(condition=condition.strip(), status="pending", error="", evidence="")
        if "kind" in options:
            if options["kind"] not in ("bot", "user"):
                raise ValueError("事项类型必须为 bot 或 user")
            item["kind"] = options["kind"]
        if "due_at" in options and "delay_seconds" in options:
            raise ValueError("绝对时间和相对时间只能选择一个")
        if "due_at" in options or "delay_seconds" in options:
            if "delay_seconds" in options:
                delay = options["delay_seconds"]
                if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay <= 0:
                    raise ValueError("相对时间必须为大于 0 的秒数")
                due = time.time() + delay
            else:
                value = options["due_at"]
                if value is not None and not isinstance(value, str):
                    raise ValueError("绝对时间须为 ISO 8601 日期时间，或 null 取消定时")
                due = datetime.fromisoformat(value).timestamp() if value is not None else None
            if due is not None and (not math.isfinite(due) or due <= time.time()):
                raise ValueError("请设定未来时间")
            item.update(due_at=due, status="pending", error="")
        if "condition" in options and not item["condition"] and options.get("condition_type") != "app":
            item["condition_type"] = "natural"
        if item["condition_type"] == "app" and not item["condition"].strip().casefold().removesuffix(".exe"):
            raise ValueError("应用检测需要填写进程名或窗口标题关键字")
        return item

    def claim_due(self) -> List[Dict[str, Any]]:
        """持锁领取到期事项；重启时重新投递未获得回执的事项。"""
        with self._lock:
            due = [dict(item, status="dispatching") for item in self._items
                   if item["status"] == "pending" and not item["condition"] and item["due_at"] is not None
                   and item["due_at"] <= time.time()]
            claimed = {item["id"]: item for item in due}
            if due:
                self._save([claimed.get(item["id"], item) for item in self._items])
            return due

    def claim_condition(self, todo_id: str, revision: str, evidence: str) -> Dict[str, Any]:
        """模型判断条件后原子领取；版本检查阻止旧上下文触发修改后的事项。"""
        if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 1000:
            raise ValueError("请提供 1～1000 字的当前条件成立依据")
        with self._lock:
            item = next((entry for entry in self._items if entry["id"] == todo_id), None)
            if item is None or item["revision"] != revision:
                raise ValueError("待办已修改或删除，请重新查询")
            if not item["condition"] or item["status"] != "pending":
                raise ValueError("此事项不是待触发的条件待办")
            if item["due_at"] is not None and item["due_at"] > time.time():
                raise ValueError("尚未到达允许触发的时间")
            claimed = dict(item, status="dispatching", evidence=evidence.strip())
            self._save([claimed if entry["id"] == todo_id else entry for entry in self._items])
            return dict(claimed)

    def finish(self, todo_id: str, revision: str, error: str = "") -> None:
        with self._lock:
            self._save([dict(item, status="failed" if error else "triggered", error=error)
                        if item["id"] == todo_id and item["revision"] == revision else item
                        for item in self._items])

    def done(self, todo_id: str) -> None:
        with self._lock:
            if not any(item["id"] == todo_id for item in self._items):
                raise ValueError("待办不存在，请重新查询列表")
            self._save([item for item in self._items if item["id"] != todo_id])

    def _save(self, items: List[Dict[str, Any]]) -> None:
        # 先持久化再替换内存状态，写入失败不能显示为操作成功。
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(items, ensure_ascii=False), "utf-8")
        temp_path.replace(self._path)
        self._items = items

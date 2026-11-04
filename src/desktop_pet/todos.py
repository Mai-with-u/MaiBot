"""桌宠窗口线程与 Agent 指令共用的待办存储，完成后归档留存。"""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
import base64
import hashlib
import json
import math
import threading
import time
import uuid


class TodoStore:
    """待办列表，完成后归档留存。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._items: List[Dict[str, Any]] = json.loads(path.read_text("utf-8")) if path.exists() else []
        for item in self._items:
            item.setdefault("images", [])
            item.setdefault("condition_type", "natural")
            item.setdefault("condition", "")
            item.setdefault("kind", "user")
            item.setdefault("due_at", None)
            item.setdefault("status", "pending")
            item.setdefault("revision", uuid.uuid4().hex)
            if item["status"] == "dispatching":
                item["status"] = "pending"

    def list(self, include_completed: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items if include_completed or item["status"] != "completed"]

    def add(self, text: str, **options: Any) -> Dict[str, Any]:
        text = self._validate_text(text)
        item = {"id": uuid.uuid4().hex, "text": text, "created_at": time.time(), "kind": "user", "due_at": None,
                "images": [], "status": "pending", "condition": "", "condition_type": "natural", "revision": uuid.uuid4().hex}
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
            if item["status"] == "completed":
                raise ValueError("已完成事项不可修改，请新增待办")
            updated = self._apply_options({**item, "text": item["text"] if text is None else text}, options)
            if any(updated[key] != item[key] for key in ("text", "kind", "due_at", "condition", "condition_type", "images")):
                updated["updated_at"] = time.time()
            updated["revision"] = uuid.uuid4().hex
            if updated["status"] == "dispatching":
                updated["status"] = "pending"
            self._save([updated if entry["id"] == todo_id else entry for entry in self._items])
            return dict(updated)

    def _apply_options(self, item: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
        if set(options) - {"kind", "due_at", "delay_seconds", "condition", "condition_type", "images"}:
            raise ValueError("未知待办字段")
        if "images" in options:
            images = options["images"]
            if not isinstance(images, list) or len(images) > 4:
                raise ValueError("每个事项最多保存 4 张图片")
            item["images"] = [self._store_image(image) for image in images]
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

    def _store_image(self, source: str) -> str:
        from PIL import Image

        if not isinstance(source, str):
            raise ValueError("图片格式无效")
        if not source.startswith("data:"):
            self.image(source)  # 已保存附件必须确实存在，禁止任意文件路径。
            return source
        if len(source) > 12 * 1024 * 1024:
            raise ValueError("每张图片不能超过 8 MiB")
        try:
            header, encoded = source.split(",", 1)
            if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64", "data:image/gif;base64"}:
                raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片")
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("每张图片不能超过 8 MiB")
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"} or image.width * image.height > 20_000_000:
                    raise ValueError("图片格式不支持或尺寸超过 2000 万像素")
                image.verify()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("无法读取图片") from exc
        image_id = hashlib.sha256(raw).hexdigest()
        folder = self._path.parent / "todo_images"
        folder.mkdir(exist_ok=True)
        path = folder / image_id
        if not path.exists():
            path.write_bytes(raw)
        return image_id

    def image(self, image_id: str) -> str:
        from PIL import Image

        if len(image_id) != 64 or any(char not in "0123456789abcdef" for char in image_id):
            raise ValueError("图片标识无效")
        raw = (self._path.parent / "todo_images" / image_id).read_bytes()
        with Image.open(BytesIO(raw)) as image:
            mime = Image.MIME[image.format]
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

    def claim_due(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """持锁领取到期事项；重启时重新投递未获得回执的事项。"""
        with self._lock:
            due = [dict(item, status="dispatching") for item in self._items
                   if item["status"] == "pending" and not item["condition"] and item["due_at"] is not None
                   and item["due_at"] <= time.time() and (kind is None or item["kind"] == kind)]
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
            item = next((entry for entry in self._items if entry["id"] == todo_id), None)
            if item is None:
                raise ValueError("待办不存在，请重新查询列表")
            if item["status"] == "completed":
                return
            # 更换版本以阻止迟到的触发回执覆盖完成状态，保留原内容和触发信息。
            completed = dict(item, status="completed", completed_at=time.time(), revision=uuid.uuid4().hex)
            self._save([completed if entry["id"] == todo_id else entry for entry in self._items])

    def delete(self, todo_id: str) -> None:
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

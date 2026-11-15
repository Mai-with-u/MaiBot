"""按真实聊天流分文件保存日程，并迁移旧桌宠待办。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import json
import os

from src.common.atomic_file import sync_directory
from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID

from .commands import execute_todo
from .store import TodoStore

logger = get_logger("schedule")


def schedule_path(directory: Path, session_id: str) -> Path:
    """会话 ID 仅作为文件名，禁止访问日程目录之外的路径。"""
    if not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in session_id):
        raise ValueError("日程会话 ID 必须为非空的字母、数字、下划线或连字符")
    return directory / f"{session_id}.json"


def owner_session_id() -> str:
    """旧桌宠事项与窗口新增事项只归已有的真实主人会话。"""
    from src.chat.message_receive.chat_manager import chat_manager

    sessions = chat_manager.resolve_sessions_by_target(
        platform=INTERNAL_CHAT_PLATFORM, target_id=INTERNAL_CHAT_USER_ID, chat_type="private",
    )
    if len(sessions) != 1:
        raise ValueError("无法确定日程所属的主人聊天流，请先建立唯一的主人会话")
    return sessions[0].session_id


def read_schedule_items(directory: Path, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """供工作线程只读最新快照，不创建写入实例或修改状态。"""
    paths = [schedule_path(directory, session_id)] if session_id is not None else sorted(directory.glob("*.json"))
    items: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError(f"日程文件必须是 JSON 对象数组: {path}")
        if any(item["session_id"] != path.stem for item in data):
            raise ValueError(f"日程会话归属与文件名不一致: {path}")
        items.extend(item for item in data if item["status"] not in {"completed", "cancelled"})
    return items


class ScheduleRepository:
    """服务持有每个会话唯一的存储实例；单次修改只写对应会话文件。"""

    def __init__(self, directory: Path, legacy_path: Optional[Path] = None) -> None:
        self.directory = directory
        self._stores: Dict[str, TodoStore] = {}
        for path in sorted(directory.glob("*.json")):
            store = self._session_store(path.stem)
            if any(item["session_id"] != path.stem for item in store.list(True)):
                raise ValueError(f"日程会话归属与文件名不一致: {path}")
        if legacy_path is not None and legacy_path.exists():
            self._migrate(legacy_path)

    def _session_store(self, session_id: str) -> TodoStore:
        path = schedule_path(self.directory, session_id)
        if session_id not in self._stores:
            self._stores[session_id] = TodoStore(path)
        return self._stores[session_id]

    def _migrate(self, legacy_path: Path) -> None:
        legacy = TodoStore(legacy_path)
        if not legacy_path.exists():
            return
        groups: Dict[str, List[Dict[str, Any]]] = {}
        owner: Optional[str] = None
        for item in legacy.list(True):
            session_id = item["session_id"]
            if not session_id:
                if owner is None:
                    owner = owner_session_id()
                session_id = owner
                item = {**item, "session_id": session_id}
            schedule_path(self.directory, session_id)
            groups.setdefault(session_id, []).append(item)
        # 先复制附件和提交各会话，再归档旧文件；中断后按原 ID 继续，不覆盖已迁移的记录。
        for session_id, items in groups.items():
            for item in items:
                for image_id in item["images"]:
                    source = legacy_path.parent / "todo_images" / image_id
                    destination = self.directory / "todo_images" / image_id
                    if len(image_id) != 64 or any(char not in "0123456789abcdef" for char in image_id):
                        raise ValueError("旧日程图片标识无效")
                    raw = source.read_bytes()
                    if not destination.exists():
                        write_private_bytes(destination, raw)
            store = self._session_store(session_id)
            existing = store.list(True)
            ids = {item["id"] for item in existing}
            added = [item for item in items if item["id"] not in ids]
            if added:
                store._save([*existing, *added])
        backup = legacy_path.with_name(f"{legacy_path.name}.migrated-{uuid4().hex}.bak")
        legacy_path.rename(backup)
        sync_directory(legacy_path.parent)
        logger.info("旧日程已按会话迁移到 %s，原文件备份为 %s", self.directory, backup)

    def list(self, include_completed: bool = False, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        stores = [self._session_store(session_id)] if session_id is not None else list(self._stores.values())
        return [item for store in stores for item in store.list(include_completed)]

    def execute(self, command: Dict[str, Any], session_id: Optional[str]) -> Dict[str, Any]:
        action = command["action"]
        if action == "list":
            return {"items": self.list(command.get("include_completed", False), session_id)}
        if action == "claim_due":
            if session_id is None:
                raise ValueError("领取到期日程必须指定会话")
            return {"items": self._session_store(session_id).claim_due(command.get("kind"))}
        if action == "image":
            # 所有会话附件按内容哈希去重；窗口仅通过主程序读取。
            return {"data_url": TodoStore.read_image(self.directory / "todo_images", command["image_id"])}
        if action == "add":
            options = dict(command.get("options", {}))
            target = session_id or options.get("session_id") or owner_session_id()
            if options.get("session_id", target) != target:
                raise ValueError("不能修改日程所属聊天")
            options["session_id"] = target
            if "session_name" not in options:
                from src.chat.message_receive.chat_manager import chat_manager

                name = chat_manager.get_session_name(target)
                if not name:
                    raise ValueError("无法获取真实聊天名称")
                options["session_name"] = name
            return execute_todo(self._session_store(target), {**command, "options": options})
        options = command.get("options", {})
        for sid, store in self._stores.items():
            if session_id is not None and session_id != sid:
                continue
            if any(item["id"] == command["todo_id"] for item in store.list(True)):
                if options.get("session_id", sid) != sid:
                    raise ValueError("不能修改日程所属聊天")
                return execute_todo(store, command)
        raise ValueError("当前聊天中不存在此日程，请先查询真实 ID")


def write_private_bytes(path: Path, content: bytes) -> None:
    """迁移的附件先写临时文件，再原子提交。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def schedules_directory() -> Path:
    return get_instance_root() / "data" / "schedules"

"""按真实聊天流持久化 ACP 会话身份及已同步内容。"""

from collections import Counter
from contextlib import suppress
from typing import Any, Dict, List, Optional, Sequence, Tuple

import asyncio
import hashlib
import json

from src.common.async_work import run_sync_work
from src.common.paths import get_instance_root
from src.llm_models.payload_content.context_item import ContextItem, SystemMessageItem
from src.plugin_runtime.hook_payloads import serialize_prompt_items

from src.common.json_storage import read_json, write_json

_locks: Dict[str, asyncio.Lock] = {}
_active: Dict[str, asyncio.Task[Any]] = {}


class PlannerState:
    def __init__(self, session_id: str) -> None:
        # 哈希仅作安全文件名；归属仍使用调用方传入的真实聊天流 ID。
        name = hashlib.sha256(session_id.encode()).hexdigest()
        self.path = get_instance_root() / "data/agent/planner" / f"{name}.json"
        self.session_id = session_id
        self.lock = _locks.setdefault(str(self.path), asyncio.Lock())
        self.data: Dict[str, Any] = {}

    async def load(self) -> None:
        def read() -> Dict[str, Any]:
            return read_json(self.path) if self.path.exists() else {
                "chat_id": self.session_id, "seen": {}, "policy": "", "pending": None,
            }
        self.data = await run_sync_work(read)
        if self.data["chat_id"] != self.session_id:
            raise ValueError("外接会话归属不匹配")

    async def save(self) -> None:
        def write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, self.data)
        # 取消时必须等原子提交结束，才能释放同一聊天的锁。
        await run_sync_work(write)

    def bind(self) -> None:
        _active[str(self.path)] = asyncio.current_task()

    def unbind(self) -> None:
        _active.pop(str(self.path), None)


def content_keys(items: Sequence[ContextItem], history_item_ids: Sequence[str] = ()) -> List[str]:
    keys = []
    stable_ids = set(history_item_ids)
    for item, payload in zip(items, serialize_prompt_items(items), strict=True):
        payload.pop("meta", None)
        if item.meta.item_id in stable_ids:
            payload["history_id"] = item.meta.item_id
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        keys.append(hashlib.sha256(raw.encode()).hexdigest())
    return keys


def select_delta(
    items: Sequence[ContextItem], seen: Dict[str, int], previous_system: Sequence[str] = (),
    history_item_ids: Sequence[str] = (),
    previous_transient: Optional[Dict[str, int]] = None,
) -> Tuple[List[ContextItem], Dict[str, int]]:
    """保留相同内容在同一上下文中的出现次数，不按集合误吞重复消息。"""
    counts: Counter[str] = Counter()
    updated = dict(seen)
    delta = []
    keys = content_keys(items, history_item_ids)
    stable_ids = set(history_item_ids)
    system_changed = [key for item, key in zip(items, keys, strict=True)
                      if isinstance(item, SystemMessageItem)] != list(previous_system)
    for item, key in zip(items, keys, strict=True):
        counts[key] += 1
        known = seen if item.meta.item_id in stable_ids or previous_transient is None else previous_transient
        if counts[key] > known.get(key, 0) or (system_changed and isinstance(item, SystemMessageItem)):
            delta.append(item)
        updated[key] = max(counts[key], updated.get(key, 0))
    return delta, updated


async def reset_planner_session(session_id: str) -> None:
    state = PlannerState(session_id)
    task = _active.get(str(state.path))
    if task is not None and task is not asyncio.current_task():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    async with state.lock:
        # 保留外部历史供人工查看；解除绑定后下一次输入建立新的空会话。
        await run_sync_work(state.path.unlink, missing_ok=True)

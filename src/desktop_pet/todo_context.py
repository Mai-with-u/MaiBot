"""每轮重建主人私聊的待办上下文，不依赖历史窗口或桌宠是否正在运行。"""

from datetime import datetime
import json

from src.common.prompt_i18n import load_prompt
from src.services.schedules.repository import read_schedule_items, schedules_directory

from .chat_tools import is_owner_chat


def build_todo_context(session_id: str) -> str:
    """由上下文构建工作线程调用；只读原子保存的文件，不创建第二个写入实例。"""
    if not is_owner_chat(session_id):
        return ""
    items = read_schedule_items(schedules_directory(), session_id)
    for item in items:
        if item.get("due_at") is not None:
            item["due_at_local"] = datetime.fromtimestamp(item["due_at"]).astimezone().isoformat()
    return load_prompt("desktop_pet_todo_context", todos=json.dumps(items, ensure_ascii=False))

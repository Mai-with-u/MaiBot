from typing import Any, Dict, Optional

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.plugin_system.apis import database_api

logger = get_logger("dream_agent")


def make_update_chat_history(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def update_chat_history(
        memory_id: int,
        theme: Optional[str] = None,
        summary: Optional[str] = None,
        keywords: Optional[str] = None,
        key_point: Optional[str] = None,
    ) -> str:
        """按字段更新 chat_history（字符串字段要求 JSON 的字段须传入已序列化的字符串）"""
        try:
            logger.info(
                f"[dream][tool] 调用 update_chat_history(memory_id={memory_id}, "
                f"theme={bool(theme)}, summary={bool(summary)}, keywords={bool(keywords)}, key_point={bool(key_point)})"
            )
            record = ChatHistory.get_or_none(ChatHistory.id == memory_id)
            if not record:
                msg = f"未找到 ID={memory_id} 的 ChatHistory 记录，无法更新。"
                logger.info(f"[dream][tool] update_chat_history 未找到记录: {msg}")
                return msg

            data: Dict[str, Any] = {}
            if theme is not None:
                data["theme"] = theme
            if summary is not None:
                data["summary"] = summary
            if keywords is not None:
                data["keywords"] = keywords
            if key_point is not None:
                data["key_point"] = key_point

            if not data:
                return "未提供任何需要更新的字段。"

            await database_api.db_save(ChatHistory, data=data, key_field="id", key_value=memory_id)
            msg = f"已更新 ChatHistory 记录 ID={memory_id}，更新字段={list(data.keys())}。"
            logger.info(f"[dream][tool] update_chat_history 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"update_chat_history 失败: {e}")
            return f"update_chat_history 执行失败: {e}"

    return update_chat_history

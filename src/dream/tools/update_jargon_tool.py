from typing import Any, Dict, Optional

from src.common.logger import get_logger
from src.common.database.database_model import Jargon
from src.plugin_system.apis import database_api

logger = get_logger("dream_agent")


def make_update_jargon(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def update_jargon(
        jargon_id: int,
        meaning: Optional[str] = None,
        is_global: Optional[bool] = None,
        is_jargon: Optional[bool] = None,
        content: Optional[str] = None,
    ) -> str:
        """按字段更新 Jargon 记录，可用于修正含义、调整全局性、标记是否为黑话等"""
        try:
            logger.info(
                f"[dream][tool] 调用 update_jargon(jargon_id={jargon_id}, "
                f"meaning={bool(meaning)}, is_global={is_global}, is_jargon={is_jargon}, content={bool(content)})"
            )
            record = Jargon.get_or_none(Jargon.id == jargon_id)
            if not record:
                msg = f"未找到 ID={jargon_id} 的 Jargon 记录，无法更新。"
                logger.info(f"[dream][tool] update_jargon 未找到记录: {msg}")
                return msg

            data: Dict[str, Any] = {}
            if meaning is not None:
                data["meaning"] = meaning
            if is_global is not None:
                data["is_global"] = is_global
            if is_jargon is not None:
                data["is_jargon"] = is_jargon
            if content is not None:
                data["content"] = content

            if not data:
                return "未提供任何需要更新的字段。"

            await database_api.db_save(Jargon, data=data, key_field="id", key_value=jargon_id)
            msg = f"已更新 Jargon 记录 ID={jargon_id}，更新字段={list(data.keys())}。"
            logger.info(f"[dream][tool] update_jargon 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"update_jargon 失败: {e}")
            return f"update_jargon 执行失败: {e}"

    return update_jargon

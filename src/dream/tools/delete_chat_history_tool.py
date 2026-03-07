from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory

logger = get_logger("dream_agent")


def make_delete_chat_history(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def delete_chat_history(memory_id: int) -> str:
        """删除一条 chat_history 记录"""
        try:
            logger.info(f"[dream][tool] 调用 delete_chat_history(memory_id={memory_id})")
            record = ChatHistory.get_or_none(ChatHistory.id == memory_id)
            if not record:
                msg = f"未找到 ID={memory_id} 的 ChatHistory 记录，无法删除。"
                logger.info(f"[dream][tool] delete_chat_history 未找到记录: {msg}")
                return msg
            rows = ChatHistory.delete().where(ChatHistory.id == memory_id).execute()
            msg = f"已删除 ID={memory_id} 的 ChatHistory 记录，受影响行数={rows}。"
            logger.info(f"[dream][tool] delete_chat_history 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"delete_chat_history 失败: {e}")
            return f"delete_chat_history 执行失败: {e}"

    return delete_chat_history

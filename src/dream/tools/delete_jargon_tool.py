from src.common.logger import get_logger
from src.common.database.database_model import Jargon

logger = get_logger("dream_agent")


def make_delete_jargon(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def delete_jargon(jargon_id: int) -> str:
        """删除一条 Jargon 记录"""
        try:
            logger.info(f"[dream][tool] 调用 delete_jargon(jargon_id={jargon_id})")
            record = Jargon.get_or_none(Jargon.id == jargon_id)
            if not record:
                msg = f"未找到 ID={jargon_id} 的 Jargon 记录，无法删除。"
                logger.info(f"[dream][tool] delete_jargon 未找到记录: {msg}")
                return msg
            rows = Jargon.delete().where(Jargon.id == jargon_id).execute()
            msg = f"已删除 ID={jargon_id} 的 Jargon 记录（内容：{record.content}），受影响行数={rows}。"
            logger.info(f"[dream][tool] delete_jargon 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"delete_jargon 失败: {e}")
            return f"delete_jargon 执行失败: {e}"

    return delete_jargon

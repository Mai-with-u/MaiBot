import time

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory

logger = get_logger("dream_agent")


def make_get_chat_history_detail(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def get_chat_history_detail(memory_id: int) -> str:
        """获取单条 chat_history 的完整内容"""
        try:
            logger.info(f"[dream][tool] 调用 get_chat_history_detail(memory_id={memory_id})")
            record = ChatHistory.get_or_none(ChatHistory.id == memory_id)
            if not record:
                msg = f"未找到 ID={memory_id} 的 ChatHistory 记录。"
                logger.info(f"[dream][tool] get_chat_history_detail 未找到记录: {msg}")
                return msg

            # 将时间戳转换为可读时间格式
            start_time_str = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.start_time)) if record.start_time else "未知"
            )
            end_time_str = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.end_time)) if record.end_time else "未知"
            )

            result = (
                f"ID={record.id}\n"
                # f"chat_id={record.chat_id}\n"
                f"时间范围={start_time_str} 至 {end_time_str}\n"
                f"主题={record.theme or '无'}\n"
                f"关键词={record.keywords or '无'}\n"
                f"参与者={record.participants or '无'}\n"
                f"概括={record.summary or '无'}\n"
                f"关键信息={record.key_point or '无'}"
            )
            logger.debug(f"[dream][tool] get_chat_history_detail 成功，预览: {result[:200].replace(chr(10), ' ')}")
            return result
        except Exception as e:
            logger.error(f"get_chat_history_detail 失败: {e}")
            return f"get_chat_history_detail 执行失败: {e}"

    return get_chat_history_detail

import time

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory

logger = get_logger("dream_agent")


def make_create_chat_history(chat_id: str):
    async def create_chat_history(
        theme: str,
        summary: str,
        keywords: str,
        key_point: str,
        start_time: float,
        end_time: float,
    ) -> str:
        """创建一条新的 ChatHistory 概括记录（用于整理/合并后的新记忆）"""
        try:
            logger.info(
                f"[dream][tool] 调用 create_chat_history("
                f"theme={bool(theme)}, summary={bool(summary)}, "
                f"keywords={bool(keywords)}, key_point={bool(key_point)}, "
                f"start_time={start_time}, end_time={end_time}) (chat_id={chat_id})"
            )

            now_ts = time.time()

            # 将传入的 start_time/end_time（如果有）解析为时间戳；否则回退为当前时间
            def _parse_ts(value, default):
                if value is None:
                    return default
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            start_ts = _parse_ts(start_time, now_ts)
            end_ts = _parse_ts(end_time, now_ts)

            record = ChatHistory.create(
                chat_id=chat_id,
                theme=theme,
                summary=summary,
                keywords=keywords,
                key_point=key_point,
                # 对于由 dream 整理产生的新概括，时间范围优先使用工具提供的时间，否则使用当前时间占位
                start_time=start_ts,
                end_time=end_ts,
            )

            msg = (
                f"已创建新的 ChatHistory 记录，ID={record.id}，"
                f"theme={record.theme or '无'}，summary={'有' if record.summary else '无'}。"
            )
            logger.info(f"[dream][tool] create_chat_history 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"create_chat_history 失败: {e}")
            return f"create_chat_history 执行失败: {e}"

    return create_chat_history

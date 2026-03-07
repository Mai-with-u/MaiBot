from typing import Optional

from src.common.logger import get_logger

logger = get_logger("dream_agent")


def make_finish_maintenance(chat_id: str):  # chat_id 目前未直接使用，预留以备扩展
    async def finish_maintenance(reason: Optional[str] = None) -> str:
        """结束本次 dream 维护任务。当你认为当前 chat_id 下的维护工作已经完成，没有更多需要整理的内容时，调用此工具来结束本次运行。"""
        reason_text = f"，原因：{reason}" if reason else ""
        msg = f"DREAM_MAINTENANCE_COMPLETE{reason_text}"
        logger.info(f"[dream][tool] 调用 finish_maintenance，结束本次维护{reason_text}")
        return msg

    return finish_maintenance

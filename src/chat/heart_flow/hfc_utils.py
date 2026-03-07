from dataclasses import dataclass
from typing import Optional, Dict, Any

import time

from src.config.config import global_config
from src.common.logger import get_logger
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.services import send_service as send_api

from src.common.message_repository import count_messages

logger = get_logger(__name__)

@dataclass
class CyclePlanInfo:
    ...
    
@dataclass
class CycleActionInfo:
    ...

class CycleDetail:
    """循环信息记录类"""

    def __init__(self, cycle_id: int):
        self.cycle_id = cycle_id
        self.thinking_id = ""
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.timers: Dict[str, float] = {}

        self.loop_plan_info: CyclePlanInfo = CyclePlanInfo()
        self.loop_action_info: CycleActionInfo = CycleActionInfo()

    def to_dict(self) -> Dict[str, Any]:
        """将循环信息转换为字典格式"""

        def convert_to_serializable(obj, depth=0, seen=None):
            if seen is None:
                seen = set()

            # 防止递归过深
            if depth > 5:  # 降低递归深度限制
                return str(obj)

            # 防止循环引用
            obj_id = id(obj)
            if obj_id in seen:
                return str(obj)
            seen.add(obj_id)

            try:
                if hasattr(obj, "to_dict"):
                    # 对于有to_dict方法的对象，直接调用其to_dict方法
                    return obj.to_dict()
                elif isinstance(obj, dict):
                    # 对于字典，只保留基本类型和可序列化的值
                    return {
                        k: convert_to_serializable(v, depth + 1, seen)
                        for k, v in obj.items()
                        if isinstance(k, (str, int, float, bool))
                    }
                elif isinstance(obj, (list, tuple)):
                    # 对于列表和元组，只保留可序列化的元素
                    return [
                        convert_to_serializable(item, depth + 1, seen)
                        for item in obj
                        if not isinstance(item, (dict, list, tuple))
                        or isinstance(item, (str, int, float, bool, type(None)))
                    ]
                elif isinstance(obj, (str, int, float, bool, type(None))):
                    return obj
                else:
                    return str(obj)
            finally:
                seen.remove(obj_id)

        return {
            "cycle_id": self.cycle_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "timers": self.timers,
            "thinking_id": self.thinking_id,
            "loop_plan_info": convert_to_serializable(self.loop_plan_info),
            "loop_action_info": convert_to_serializable(self.loop_action_info),
        }

    def set_loop_info(self, loop_info: Dict[str, Any]):
        """设置循环信息"""
        self.loop_plan_info = loop_info["loop_plan_info"]
        self.loop_action_info = loop_info["loop_action_info"]


def get_recent_message_stats(minutes: float = 30, chat_id: Optional[str] = None) -> dict:
    """
    Args:
        minutes (float): 检索的分钟数，默认30分钟
        chat_id (str, optional): 指定的chat_id，仅统计该chat下的消息。为None时统计全部。
    Returns:
        dict: {"bot_reply_count": int, "total_message_count": int}
    """

    now = time.time()
    start_time = now - minutes * 60
    bot_id = global_config.bot.qq_account

    filter_base: Dict[str, Any] = {"time": {"$gte": start_time}}
    if chat_id is not None:
        filter_base["chat_id"] = chat_id

    # 总消息数
    total_message_count = count_messages(filter_base)
    # bot自身回复数
    bot_filter = filter_base.copy()
    bot_filter["user_id"] = bot_id
    bot_reply_count = count_messages(bot_filter)

    return {"bot_reply_count": bot_reply_count, "total_message_count": total_message_count}


async def send_typing():
    chat = await _chat_manager.get_or_create_session(
        platform="amaidesu_default",
        user_id="114514",
        group_id="114514",
    )

    await send_api.custom_to_stream(
        message_type="state", content="typing", stream_id=chat.session_id, storage_message=False
    )


async def stop_typing():
    chat = await _chat_manager.get_or_create_session(
        platform="amaidesu_default",
        user_id="114514",
        group_id="114514",
    )

    await send_api.custom_to_stream(
        message_type="state", content="stop_typing", stream_id=chat.session_id, storage_message=False
    )

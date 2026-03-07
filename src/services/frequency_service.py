"""频率控制服务模块

提供聊天频率控制的核心功能。
"""

from src.chat.heart_flow.frequency_control import frequency_control_manager
from src.config.config import global_config


def get_current_talk_value(chat_id: str) -> float:
    return frequency_control_manager.get_or_create_frequency_control(
        chat_id
    ).get_talk_frequency_adjust() * global_config.chat.get_talk_value(chat_id)


def set_talk_frequency_adjust(chat_id: str, talk_frequency_adjust: float) -> None:
    frequency_control_manager.get_or_create_frequency_control(chat_id).set_talk_frequency_adjust(talk_frequency_adjust)


def get_talk_frequency_adjust(chat_id: str) -> float:
    return frequency_control_manager.get_or_create_frequency_control(chat_id).get_talk_frequency_adjust()

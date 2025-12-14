from typing import Dict

from src.common.logger import get_logger

logger = get_logger("frequency_control")


class FrequencyControl:
    """简化的频率控制类，仅管理不同chat_id的频率值"""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        # 发言频率调整值
        self.talk_frequency_adjust: float = 1.0

    def get_talk_frequency_adjust(self) -> float:
        """获取发言频率调整值"""
        return self.talk_frequency_adjust

    def set_talk_frequency_adjust(self, value: float) -> None:
        """设置发言频率调整值"""
        self.talk_frequency_adjust = max(0.1, min(5.0, value))


class FrequencyControlManager:
    """频率控制管理器，管理多个聊天流的频率控制实例"""

    def __init__(self):
        self.frequency_control_dict: Dict[str, FrequencyControl] = {}

    def get_or_create_frequency_control(self, chat_id: str) -> FrequencyControl:
        """获取或创建指定聊天流的频率控制实例"""
        if chat_id not in self.frequency_control_dict:
            self.frequency_control_dict[chat_id] = FrequencyControl(chat_id)
        return self.frequency_control_dict[chat_id]

    def remove_frequency_control(self, chat_id: str) -> bool:
        """移除指定聊天流的频率控制实例"""
        if chat_id in self.frequency_control_dict:
            del self.frequency_control_dict[chat_id]
            return True
        return False

    def get_all_chat_ids(self) -> list[str]:
        """获取所有有频率控制的聊天ID"""
        return list(self.frequency_control_dict.keys())


# 创建全局实例
frequency_control_manager = FrequencyControlManager()

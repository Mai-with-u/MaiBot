"""
消息服务模块

提供消息查询和构建成字符串的核心功能。
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import col, select

from src.chat.utils.chat_message_builder import (
    build_readable_messages,
    build_readable_messages_with_list,
    get_person_id_list,
    get_raw_msg_before_timestamp,
    get_raw_msg_before_timestamp_with_chat,
    get_raw_msg_before_timestamp_with_users,
    get_raw_msg_by_timestamp,
    get_raw_msg_by_timestamp_random,
    get_raw_msg_by_timestamp_with_chat,
    get_raw_msg_by_timestamp_with_chat_inclusive,
    get_raw_msg_by_timestamp_with_chat_users,
    get_raw_msg_by_timestamp_with_users,
    num_new_messages_since,
    num_new_messages_since_with_users,
)
from src.chat.utils.utils import is_bot_self
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType


# =============================================================================
# 消息查询函数
# =============================================================================


def get_messages_by_time(
    start_time: float, end_time: float, limit: int = 0, limit_mode: str = "latest", filter_mai: bool = False
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if filter_mai:
        return filter_mai_messages(get_raw_msg_by_timestamp(start_time, end_time, limit, limit_mode))
    return get_raw_msg_by_timestamp(start_time, end_time, limit, limit_mode)


def get_messages_by_time_in_chat(
    chat_id: str,
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_mai: bool = False,
    filter_command: bool = False,
    filter_intercept_message_level: Optional[int] = None,
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    return get_raw_msg_by_timestamp_with_chat(
        chat_id=chat_id,
        timestamp_start=start_time,
        timestamp_end=end_time,
        limit=limit,
        limit_mode=limit_mode,
        filter_bot=filter_mai,
        filter_command=filter_command,
        filter_intercept_message_level=filter_intercept_message_level,
    )


def get_messages_by_time_in_chat_inclusive(
    chat_id: str,
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_mai: bool = False,
    filter_command: bool = False,
    filter_intercept_message_level: Optional[int] = None,
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    messages = get_raw_msg_by_timestamp_with_chat_inclusive(
        chat_id=chat_id,
        timestamp_start=start_time,
        timestamp_end=end_time,
        limit=limit,
        limit_mode=limit_mode,
        filter_bot=filter_mai,
        filter_command=filter_command,
        filter_intercept_message_level=filter_intercept_message_level,
    )
    if filter_mai:
        return filter_mai_messages(messages)
    return messages


def get_messages_by_time_in_chat_for_users(
    chat_id: str,
    start_time: float,
    end_time: float,
    person_ids: List[str],
    limit: int = 0,
    limit_mode: str = "latest",
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    return get_raw_msg_by_timestamp_with_chat_users(chat_id, start_time, end_time, person_ids, limit, limit_mode)


def get_random_chat_messages(
    start_time: float, end_time: float, limit: int = 0, limit_mode: str = "latest", filter_mai: bool = False
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if filter_mai:
        return filter_mai_messages(get_raw_msg_by_timestamp_random(start_time, end_time, limit, limit_mode))
    return get_raw_msg_by_timestamp_random(start_time, end_time, limit, limit_mode)


def get_messages_by_time_for_users(
    start_time: float, end_time: float, person_ids: List[str], limit: int = 0, limit_mode: str = "latest"
) -> List[DatabaseMessages]:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    return get_raw_msg_by_timestamp_with_users(start_time, end_time, person_ids, limit, limit_mode)


def get_messages_before_time(timestamp: float, limit: int = 0, filter_mai: bool = False) -> List[DatabaseMessages]:
    if not isinstance(timestamp, (int, float)):
        raise ValueError("timestamp 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if filter_mai:
        return filter_mai_messages(get_raw_msg_before_timestamp(timestamp, limit))
    return get_raw_msg_before_timestamp(timestamp, limit)


def get_messages_before_time_in_chat(
    chat_id: str,
    timestamp: float,
    limit: int = 0,
    filter_mai: bool = False,
    filter_intercept_message_level: Optional[int] = None,
) -> List[DatabaseMessages]:
    if not isinstance(timestamp, (int, float)):
        raise ValueError("timestamp 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    messages = get_raw_msg_before_timestamp_with_chat(
        chat_id=chat_id,
        timestamp=timestamp,
        limit=limit,
        filter_intercept_message_level=filter_intercept_message_level,
    )
    if filter_mai:
        return filter_mai_messages(messages)
    return messages


def get_messages_before_time_for_users(
    timestamp: float, person_ids: List[str], limit: int = 0
) -> List[DatabaseMessages]:
    if not isinstance(timestamp, (int, float)):
        raise ValueError("timestamp 必须是数字类型")
    if limit < 0:
        raise ValueError("limit 不能为负数")
    return get_raw_msg_before_timestamp_with_users(timestamp, person_ids, limit)


def get_recent_messages(
    chat_id: str, hours: float = 24.0, limit: int = 100, limit_mode: str = "latest", filter_mai: bool = False
) -> List[DatabaseMessages]:
    if not isinstance(hours, (int, float)) or hours < 0:
        raise ValueError("hours 不能是负数")
    if not isinstance(limit, int) or limit < 0:
        raise ValueError("limit 必须是非负整数")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    now = time.time()
    start_time = now - hours * 3600
    if filter_mai:
        return filter_mai_messages(get_raw_msg_by_timestamp_with_chat(chat_id, start_time, now, limit, limit_mode))
    return get_raw_msg_by_timestamp_with_chat(chat_id, start_time, now, limit, limit_mode)


# =============================================================================
# 消息计数函数
# =============================================================================


def count_new_messages(chat_id: str, start_time: float = 0.0, end_time: Optional[float] = None) -> int:
    if not isinstance(start_time, (int, float)):
        raise ValueError("start_time 必须是数字类型")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    return num_new_messages_since(chat_id, start_time, end_time)


def count_new_messages_for_users(chat_id: str, start_time: float, end_time: float, person_ids: List[str]) -> int:
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")
    if not chat_id:
        raise ValueError("chat_id 不能为空")
    if not isinstance(chat_id, str):
        raise ValueError("chat_id 必须是字符串类型")
    return num_new_messages_since_with_users(chat_id, start_time, end_time, person_ids)


# =============================================================================
# 消息格式化函数
# =============================================================================


def build_readable_messages_to_str(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    read_mark: float = 0.0,
    truncate: bool = False,
    show_actions: bool = False,
) -> str:
    return build_readable_messages(messages, replace_bot_name, timestamp_mode, read_mark, truncate, show_actions)


async def build_readable_messages_with_details(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    truncate: bool = False,
) -> Tuple[str, List[Tuple[float, str, str]]]:
    return await build_readable_messages_with_list(messages, replace_bot_name, timestamp_mode, truncate)


async def get_person_ids_from_messages(messages: List[Dict[str, Any]]) -> List[str]:
    return await get_person_id_list(messages)


# =============================================================================
# 消息过滤函数
# =============================================================================


def filter_mai_messages(messages: List[DatabaseMessages]) -> List[DatabaseMessages]:
    """从消息列表中移除麦麦的消息"""
    return [msg for msg in messages if not is_bot_self(msg.user_info.platform, msg.user_info.user_id)]


def translate_pid_to_description(pid: str) -> str:
    with get_db_session() as session:
        statement = (
            select(Images).where((col(Images.id) == int(pid)) & (col(Images.image_type) == ImageType.IMAGE))
            if pid.isdigit()
            else None
        )
        image = session.exec(statement).first() if statement is not None else None
    description = ""
    if image and image.description and image.description.strip():
        description = image.description.strip()
    else:
        description = "[图片]"
    return description

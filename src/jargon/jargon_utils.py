import json
from typing import List, Dict, Optional, Any

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.utils.chat_message_builder import (
    build_readable_messages,
)
from src.chat.utils.utils import parse_platform_accounts


logger = get_logger("jargon")


def parse_chat_id_list(chat_id_value: Any) -> List[List[Any]]:
    """
    解析chat_id字段，兼容旧格式（字符串）和新格式（JSON列表）

    Args:
        chat_id_value: 可能是字符串（旧格式）或JSON字符串（新格式）

    Returns:
        List[List[Any]]: 格式为 [[chat_id, count], ...] 的列表
    """
    if not chat_id_value:
        return []

    # 如果是字符串，尝试解析为JSON
    if isinstance(chat_id_value, str):
        # 尝试解析JSON
        try:
            parsed = json.loads(chat_id_value)
            if isinstance(parsed, list):
                # 新格式：已经是列表
                return parsed
            elif isinstance(parsed, str):
                # 解析后还是字符串，说明是旧格式
                return [[parsed, 1]]
            else:
                # 其他类型，当作旧格式处理
                return [[str(chat_id_value), 1]]
        except (json.JSONDecodeError, TypeError):
            # 解析失败，当作旧格式（纯字符串）
            return [[str(chat_id_value), 1]]
    elif isinstance(chat_id_value, list):
        # 已经是列表格式
        return chat_id_value
    else:
        # 其他类型，转换为旧格式
        return [[str(chat_id_value), 1]]


def update_chat_id_list(chat_id_list: List[List[Any]], target_chat_id: str, increment: int = 1) -> List[List[Any]]:
    """
    更新chat_id列表，如果target_chat_id已存在则增加计数，否则添加新条目

    Args:
        chat_id_list: 当前的chat_id列表，格式为 [[chat_id, count], ...]
        target_chat_id: 要更新或添加的chat_id
        increment: 增加的计数，默认为1

    Returns:
        List[List[Any]]: 更新后的chat_id列表
    """
    # 查找是否已存在该chat_id
    found = False
    for item in chat_id_list:
        if isinstance(item, list) and len(item) >= 1 and str(item[0]) == str(target_chat_id):
            # 找到匹配的chat_id，增加计数
            if len(item) >= 2:
                item[1] = (item[1] if isinstance(item[1], (int, float)) else 0) + increment
            else:
                item.append(increment)
            found = True
            break

    if not found:
        # 未找到，添加新条目
        chat_id_list.append([target_chat_id, increment])

    return chat_id_list


def chat_id_list_contains(chat_id_list: List[List[Any]], target_chat_id: str) -> bool:
    """
    检查chat_id列表中是否包含指定的chat_id

    Args:
        chat_id_list: chat_id列表，格式为 [[chat_id, count], ...]
        target_chat_id: 要查找的chat_id

    Returns:
        bool: 如果包含则返回True
    """
    for item in chat_id_list:
        if isinstance(item, list) and len(item) >= 1 and str(item[0]) == str(target_chat_id):
            return True
    return False


def contains_bot_self_name(content: str) -> bool:
    """
    判断词条是否包含机器人的昵称或别名
    """
    if not content:
        return False

    bot_config = getattr(global_config, "bot", None)
    if not bot_config:
        return False

    target = content.strip().lower()
    nickname = str(getattr(bot_config, "nickname", "") or "").strip().lower()
    alias_names = [str(alias or "").strip().lower() for alias in getattr(bot_config, "alias_names", []) or []]

    candidates = [name for name in [nickname, *alias_names] if name]

    return any(name in target for name in candidates if target)


def build_context_paragraph(messages: List[Any], center_index: int) -> Optional[str]:
    """
    构建包含中心消息上下文的段落（前3条+后3条），使用标准的 readable builder 输出
    """
    if not messages or center_index < 0 or center_index >= len(messages):
        return None

    context_start = max(0, center_index - 3)
    context_end = min(len(messages), center_index + 1 + 3)
    context_messages = messages[context_start:context_end]

    if not context_messages:
        return None

    try:
        paragraph = build_readable_messages(
            messages=context_messages,
            replace_bot_name=True,
            timestamp_mode="relative",
            read_mark=0.0,
            truncate=False,
            show_actions=False,
            show_pic=True,
            message_id_list=None,
            remove_emoji_stickers=False,
            pic_single=True,
        )
    except Exception as e:
        logger.warning(f"构建上下文段落失败: {e}")
        return None

    paragraph = paragraph.strip()
    return paragraph or None


def is_bot_message(msg: Any) -> bool:
    """判断消息是否来自机器人自身"""
    if msg is None:
        return False

    bot_config = getattr(global_config, "bot", None)
    if not bot_config:
        return False

    platform = (
        str(getattr(msg, "user_platform", "") or getattr(getattr(msg, "user_info", None), "platform", "") or "")
        .strip()
        .lower()
    )
    user_id = str(getattr(msg, "user_id", "") or getattr(getattr(msg, "user_info", None), "user_id", "") or "").strip()

    if not platform or not user_id:
        return False

    platform_accounts = {}
    try:
        platform_accounts = parse_platform_accounts(getattr(bot_config, "platforms", []) or [])
    except Exception:
        platform_accounts = {}

    bot_accounts: Dict[str, str] = {}
    qq_account = str(getattr(bot_config, "qq_account", "") or "").strip()
    if qq_account:
        bot_accounts["qq"] = qq_account

    telegram_account = str(getattr(bot_config, "telegram_account", "") or "").strip()
    if telegram_account:
        bot_accounts["telegram"] = telegram_account

    for plat, account in platform_accounts.items():
        if account and plat not in bot_accounts:
            bot_accounts[plat] = account

    bot_account = bot_accounts.get(platform)
    return bool(bot_account and user_id == bot_account)

import re
import difflib
import random
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.utils.chat_message_builder import (
    build_readable_messages,
)
from src.chat.utils.utils import parse_platform_accounts


logger = get_logger("learner_utils")


def filter_message_content(content: Optional[str]) -> str:
    """
    过滤消息内容，移除回复、@、图片等格式

    Args:
        content: 原始消息内容

    Returns:
        str: 过滤后的内容
    """
    if not content:
        return ""

    # 移除以[回复开头、]结尾的部分，包括后面的"，说："部分
    content = re.sub(r"\[回复.*?\]，说：\s*", "", content)
    # 移除@<...>格式的内容
    content = re.sub(r"@<[^>]*>", "", content)
    # 移除[picid:...]格式的图片ID
    content = re.sub(r"\[picid:[^\]]*\]", "", content)
    # 移除[表情包：...]格式的内容
    content = re.sub(r"\[表情包：[^\]]*\]", "", content)

    return content.strip()


def calculate_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度，返回0-1之间的值
    使用SequenceMatcher计算相似度

    Args:
        text1: 第一个文本
        text2: 第二个文本

    Returns:
        float: 相似度值，范围0-1
    """
    return difflib.SequenceMatcher(None, text1, text2).ratio()


def format_create_date(timestamp: float) -> str:
    """
    将时间戳格式化为可读的日期字符串

    Args:
        timestamp: 时间戳

    Returns:
        str: 格式化后的日期字符串
    """
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return "未知时间"


def _compute_weights(population: List[Dict]) -> List[float]:
    """
    根据表达的count计算权重，范围限定在1~5之间。
    count越高，权重越高，但最多为基础权重的5倍。
    如果表达已checked，权重会再乘以3倍。
    """
    if not population:
        return []

    counts = []
    checked_flags = []
    for item in population:
        count = item.get("count", 1)
        try:
            count_value = float(count)
        except (TypeError, ValueError):
            count_value = 1.0
        counts.append(max(count_value, 0.0))
        # 获取checked状态
        checked = item.get("checked", False)
        checked_flags.append(bool(checked))

    min_count = min(counts)
    max_count = max(counts)

    if max_count == min_count:
        base_weights = [1.0 for _ in counts]
    else:
        base_weights = []
        for count_value in counts:
            # 线性映射到[1,5]区间
            normalized = (count_value - min_count) / (max_count - min_count)
            base_weights.append(1.0 + normalized * 4.0)  # 1~5

    # 如果checked，权重乘以3
    weights = []
    for base_weight, checked in zip(base_weights, checked_flags, strict=False):
        if checked:
            weights.append(base_weight * 3.0)
        else:
            weights.append(base_weight)
    return weights


def weighted_sample(population: List[Dict], k: int) -> List[Dict]:
    """
    随机抽样函数

    Args:
        population: 总体数据列表
        k: 需要抽取的数量

    Returns:
        List[Dict]: 抽取的数据列表
    """
    if not population or k <= 0:
        return []

    if len(population) <= k:
        return population.copy()

    selected: List[Dict] = []
    population_copy = population.copy()

    for _ in range(min(k, len(population_copy))):
        weights = _compute_weights(population_copy)
        total_weight = sum(weights)
        if total_weight <= 0:
            # 回退到均匀随机
            idx = random.randint(0, len(population_copy) - 1)
            selected.append(population_copy.pop(idx))
            continue

        threshold = random.uniform(0, total_weight)
        cumulative = 0.0
        for idx, weight in enumerate(weights):
            cumulative += weight
            if threshold <= cumulative:
                selected.append(population_copy.pop(idx))
                break

    return selected


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
    item = _find_chat_id_item(chat_id_list, target_chat_id)
    if item is not None:
        # 找到匹配的chat_id，增加计数
        if len(item) >= 2:
            item[1] = (item[1] if isinstance(item[1], (int, float)) else 0) + increment
        else:
            item.append(increment)
    else:
        # 未找到，添加新条目
        chat_id_list.append([target_chat_id, increment])

    return chat_id_list


def _find_chat_id_item(chat_id_list: List[List[Any]], target_chat_id: str) -> Optional[List[Any]]:
    """
    在chat_id列表中查找匹配的项（辅助函数）

    Args:
        chat_id_list: chat_id列表，格式为 [[chat_id, count], ...]
        target_chat_id: 要查找的chat_id

    Returns:
        如果找到则返回匹配的项，否则返回None
    """
    for item in chat_id_list:
        if isinstance(item, list) and len(item) >= 1 and str(item[0]) == str(target_chat_id):
            return item
    return None


def chat_id_list_contains(chat_id_list: List[List[Any]], target_chat_id: str) -> bool:
    """
    检查chat_id列表中是否包含指定的chat_id

    Args:
        chat_id_list: chat_id列表，格式为 [[chat_id, count], ...]
        target_chat_id: 要查找的chat_id

    Returns:
        bool: 如果包含则返回True
    """
    return _find_chat_id_item(chat_id_list, target_chat_id) is not None


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

    return any(name in target for name in candidates)


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

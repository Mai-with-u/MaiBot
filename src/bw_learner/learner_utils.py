import re
import difflib
import random
import json
from typing import Optional, List, Dict, Any, Tuple

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.utils.chat_message_builder import (
    build_readable_messages,
)
from src.chat.utils.utils import parse_platform_accounts
from json_repair import repair_json


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


def calculate_style_similarity(style1: str, style2: str) -> float:
    """
    计算两个 style 的相似度，返回0-1之间的值
    在计算前会移除"使用"和"句式"这两个词（参考 expression_similarity_analysis.py）
    
    Args:
        style1: 第一个 style
        style2: 第二个 style
    
    Returns:
        float: 相似度值，范围0-1
    """
    if not style1 or not style2:
        return 0.0
    
    # 移除"使用"和"句式"这两个词
    def remove_ignored_words(text: str) -> str:
        """移除需要忽略的词"""
        text = text.replace("使用", "")
        text = text.replace("句式", "")
        return text.strip()
    
    cleaned_style1 = remove_ignored_words(style1)
    cleaned_style2 = remove_ignored_words(style2)
    
    # 如果清理后文本为空，返回0
    if not cleaned_style1 or not cleaned_style2:
        return 0.0
    
    return difflib.SequenceMatcher(None, cleaned_style1, cleaned_style2).ratio()


def _compute_weights(population: List[Dict]) -> List[float]:
    """
    根据表达的count计算权重，范围限定在1~5之间。
    count越高，权重越高，但最多为基础权重的5倍。
    """
    if not population:
        return []

    counts = []
    for item in population:
        count = item.get("count", 1)
        try:
            count_value = float(count)
        except (TypeError, ValueError):
            count_value = 1.0
        counts.append(max(count_value, 0.0))

    min_count = min(counts)
    max_count = max(counts)

    if max_count == min_count:
        weights = [1.0 for _ in counts]
    else:
        weights = []
        for count_value in counts:
            # 线性映射到[1,5]区间
            normalized = (count_value - min_count) / (max_count - min_count)
            weights.append(1.0 + normalized * 4.0)  # 1~5

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


def parse_expression_response(response: str) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
    """
    解析 LLM 返回的表达风格总结和黑话 JSON，提取两个列表。

    期望的 JSON 结构：
    [
        {"situation": "AAAAA", "style": "BBBBB", "source_id": "3"},  // 表达方式
        {"content": "词条", "source_id": "12"},  // 黑话
        ...
    ]

    Returns:
        Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
            第一个列表是表达方式 (situation, style, source_id)
            第二个列表是黑话 (content, source_id)
    """
    if not response:
        return [], []

    raw = response.strip()

    # 尝试提取 ```json 代码块
    json_block_pattern = r"```json\s*(.*?)\s*```"
    match = re.search(json_block_pattern, raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()
    else:
        # 去掉可能存在的通用 ``` 包裹
        raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

    parsed = None
    expressions: List[Tuple[str, str, str]] = []  # (situation, style, source_id)
    jargon_entries: List[Tuple[str, str]] = []  # (content, source_id)

    try:
        # 优先尝试直接解析
        if raw.startswith("[") and raw.endswith("]"):
            parsed = json.loads(raw)
        else:
            repaired = repair_json(raw)
            if isinstance(repaired, str):
                parsed = json.loads(repaired)
            else:
                parsed = repaired
    except Exception as parse_error:
        # 如果解析失败，尝试修复中文引号问题
        # 使用状态机方法，在 JSON 字符串值内部将中文引号替换为转义的英文引号
        try:

            def fix_chinese_quotes_in_json(text):
                """使用状态机修复 JSON 字符串值中的中文引号"""
                result = []
                i = 0
                in_string = False
                escape_next = False

                while i < len(text):
                    char = text[i]

                    if escape_next:
                        # 当前字符是转义字符后的字符，直接添加
                        result.append(char)
                        escape_next = False
                        i += 1
                        continue

                    if char == "\\":
                        # 转义字符
                        result.append(char)
                        escape_next = True
                        i += 1
                        continue

                    if char == '"' and not escape_next:
                        # 遇到英文引号，切换字符串状态
                        in_string = not in_string
                        result.append(char)
                        i += 1
                        continue

                    if in_string:
                        # 在字符串值内部，将中文引号替换为转义的英文引号
                        if char == '"':  # 中文左引号 U+201C
                            result.append('\\"')
                        elif char == '"':  # 中文右引号 U+201D
                            result.append('\\"')
                        else:
                            result.append(char)
                    else:
                        # 不在字符串内，直接添加
                        result.append(char)

                    i += 1

                return "".join(result)

            fixed_raw = fix_chinese_quotes_in_json(raw)

            # 再次尝试解析
            if fixed_raw.startswith("[") and fixed_raw.endswith("]"):
                parsed = json.loads(fixed_raw)
            else:
                repaired = repair_json(fixed_raw)
                if isinstance(repaired, str):
                    parsed = json.loads(repaired)
                else:
                    parsed = repaired
        except Exception as fix_error:
            logger.error(f"解析表达风格 JSON 失败，初始错误: {type(parse_error).__name__}: {str(parse_error)}")
            logger.error(f"修复中文引号后仍失败，错误: {type(fix_error).__name__}: {str(fix_error)}")
            logger.error(f"解析表达风格 JSON 失败，原始响应：{response}")
            logger.error(f"处理后的 JSON 字符串（前500字符）：{raw[:500]}")
            return [], []

    if isinstance(parsed, dict):
        parsed_list = [parsed]
    elif isinstance(parsed, list):
        parsed_list = parsed
    else:
        logger.error(f"表达风格解析结果类型异常: {type(parsed)}, 内容: {parsed}")
        return [], []

    for item in parsed_list:
        if not isinstance(item, dict):
            continue

        # 检查是否是表达方式条目（有 situation 和 style）
        situation = str(item.get("situation", "")).strip()
        style = str(item.get("style", "")).strip()
        source_id = str(item.get("source_id", "")).strip()

        if situation and style and source_id:
            # 表达方式条目
            expressions.append((situation, style, source_id))
        elif item.get("content"):
            # 黑话条目（有 content 字段）
            content = str(item.get("content", "")).strip()
            source_id = str(item.get("source_id", "")).strip()
            if content and source_id:
                jargon_entries.append((content, source_id))

    return expressions, jargon_entries
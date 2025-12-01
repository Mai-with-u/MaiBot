import re
import difflib
import random
from datetime import datetime
from typing import Optional, List, Dict


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
            base_weights.append(1.0 + normalized * 4.0)  # 1~3

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

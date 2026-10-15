from typing import Dict, List

import random


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

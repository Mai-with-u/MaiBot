"""查询内多通道分数校准与融合。

该模块只处理已经召回的候选分数，不负责召回、过滤或图证据质量判断。所有方法都
保持候选集合不变，便于在同一批数据上比较标度校准对排序的独立影响。
"""

from __future__ import annotations

from math import exp, isfinite
from statistics import median
from typing import Dict, Iterable, Mapping, Tuple


SCORE_CALIBRATION_METHODS = {
    "none",
    "minmax",
    "rank_percentile",
    "robust_sigmoid",
    "semantic_minmax",
    "semantic_graph_rrf",
    "semantic_rank_percentile",
    "semantic_robust_sigmoid",
    "semantic_rrf",
    "semantic_sparse_rrf",
    "semantic_softmax",
    "softmax",
    "weighted_rrf",
}

RRF_METHOD_CHANNELS = {
    "semantic_graph_rrf": {"semantic", "graph"},
    "semantic_rrf": {"semantic"},
    "semantic_sparse_rrf": {"semantic", "sparse"},
    "weighted_rrf": None,
}


def normalize_calibration_method(method: str) -> str:
    """规范化校准方法名称，并让非法配置直接暴露。"""

    token = str(method or "none").strip().lower()
    if token not in SCORE_CALIBRATION_METHODS:
        raise ValueError(f"不支持的分数校准方法: {token}")
    return token


def _finite_score_map(scores: Mapping[str, float]) -> Dict[str, float]:
    output: Dict[str, float] = {}
    for key, value in scores.items():
        number = float(value)
        if isfinite(number):
            output[str(key)] = number
    return output


def _average_rank_scores(scores: Mapping[str, float]) -> Dict[str, float]:
    """返回并列项共享名次的降序排名分数，首名为1。"""

    if not scores:
        return {}
    ordered_values = sorted(set(scores.values()), reverse=True)
    rank_by_value = {value: index + 1 for index, value in enumerate(ordered_values)}
    return {key: float(rank_by_value[value]) for key, value in scores.items()}


def calibrate_score_map(scores: Mapping[str, float], method: str) -> Dict[str, float]:
    """将单个通道的查询内分数转换到可比较范围。"""

    normalized_method = normalize_calibration_method(method)
    finite_scores = _finite_score_map(scores)
    if not finite_scores or normalized_method == "none":
        return finite_scores
    if normalized_method in RRF_METHOD_CHANNELS:
        raise ValueError("RRF 方法需要通过 fuse_score_maps 融合通道")

    values = list(finite_scores.values())
    lower = min(values)
    upper = max(values)
    if upper - lower < 1e-12:
        return {key: 1.0 for key in finite_scores}

    if normalized_method == "minmax":
        scale = upper - lower
        return {key: (value - lower) / scale for key, value in finite_scores.items()}

    if normalized_method == "rank_percentile":
        ranks = _average_rank_scores(finite_scores)
        distinct_count = len(set(values))
        if distinct_count <= 1:
            return {key: 1.0 for key in finite_scores}
        return {
            key: 1.0 - (rank - 1.0) / float(distinct_count - 1)
            for key, rank in ranks.items()
        }

    center = float(median(values))
    deviations = [abs(value - center) for value in values]
    robust_scale = 1.4826 * float(median(deviations))
    if robust_scale < 1e-12:
        robust_scale = max((upper - lower) / 4.0, 1e-12)

    if normalized_method == "robust_sigmoid":
        calibrated: Dict[str, float] = {}
        for key, value in finite_scores.items():
            z_score = max(-12.0, min(12.0, (value - center) / robust_scale))
            calibrated[key] = 1.0 / (1.0 + exp(-z_score))
        return calibrated

    if normalized_method == "softmax":
        temperature = robust_scale
        return {
            key: exp(max(-50.0, min(0.0, (value - upper) / temperature)))
            for key, value in finite_scores.items()
        }

    raise AssertionError(f"未处理的分数校准方法: {normalized_method}")


def fuse_score_maps(
    score_maps: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
    *,
    method: str,
    rrf_k: int = 60,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """校准并融合多个候选分数通道。

    Returns:
        ``(最终分数, 各通道校准后分数)``。
    """

    normalized_method = normalize_calibration_method(method)
    finite_maps = {name: _finite_score_map(scores) for name, scores in score_maps.items()}
    candidate_ids = set()
    for scores in finite_maps.values():
        candidate_ids.update(scores)

    if normalized_method in RRF_METHOD_CHANNELS:
        safe_k = max(1, int(rrf_k))
        calibrated_channels = RRF_METHOD_CHANNELS[normalized_method]
        calibrated_maps: Dict[str, Dict[str, float]] = {}
        for channel, scores in finite_maps.items():
            if calibrated_channels is not None and channel not in calibrated_channels:
                calibrated_maps[channel] = dict(scores)
                continue
            ranks = _average_rank_scores(scores)
            calibrated_maps[channel] = {key: (safe_k + 1.0) / (safe_k + rank) for key, rank in ranks.items()}
    elif normalized_method.startswith("semantic_"):
        semantic_method = normalized_method.removeprefix("semantic_")
        calibrated_maps = {
            channel: calibrate_score_map(
                scores,
                semantic_method if channel == "semantic" else "none",
            )
            for channel, scores in finite_maps.items()
        }
    else:
        calibrated_maps = {
            channel: calibrate_score_map(scores, normalized_method)
            for channel, scores in finite_maps.items()
        }

    final_scores = {candidate_id: 0.0 for candidate_id in candidate_ids}
    for channel, calibrated_scores in calibrated_maps.items():
        weight = float(weights.get(channel, 0.0) or 0.0)
        for candidate_id, score in calibrated_scores.items():
            final_scores[candidate_id] += weight * float(score)
    return final_scores, calibrated_maps


def ordered_ids(scores: Mapping[str, float]) -> Iterable[str]:
    """按分数降序和ID升序返回稳定排序。"""

    return (
        key
        for key, _value in sorted(
            scores.items(),
            key=lambda item: (-float(item[1]), str(item[0])),
        )
    )

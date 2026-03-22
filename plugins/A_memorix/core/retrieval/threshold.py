"""
动态阈值过滤器

根据检索结果的分布特征自适应调整过滤阈值。
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum

from src.common.logger import get_logger
from .dual_path import RetrievalResult

logger = get_logger("A_Memorix.DynamicThresholdFilter")


class ThresholdMethod(Enum):
    """阈值计算方法"""

    PERCENTILE = "percentile"  # 百分位数
    STD_DEV = "std_dev"        # 标准差
    GAP_DETECTION = "gap_detection"  # 跳变检测
    ADAPTIVE = "adaptive"      # 自适应（综合多种方法）


@dataclass
class ThresholdConfig:
    """
    阈值配置

    属性：
        method: 阈值计算方法
        min_threshold: 最小阈值（绝对值）
        max_threshold: 最大阈值（绝对值）
        percentile: 百分位数（用于percentile方法）
        std_multiplier: 标准差倍数（用于std_dev方法）
        min_results: 最少保留结果数
        enable_auto_adjust: 是否自动调整参数
    """

    method: ThresholdMethod = ThresholdMethod.ADAPTIVE
    min_threshold: float = 0.3
    max_threshold: float = 0.95
    percentile: float = 75.0  # 百分位数
    std_multiplier: float = 1.5  # 标准差倍数
    min_results: int = 3  # 最少保留结果数
    enable_auto_adjust: bool = True

    def __post_init__(self):
        """验证配置"""
        if not 0 <= self.min_threshold <= 1:
            raise ValueError(f"min_threshold必须在[0, 1]之间: {self.min_threshold}")

        if not 0 <= self.max_threshold <= 1:
            raise ValueError(f"max_threshold必须在[0, 1]之间: {self.max_threshold}")

        if self.min_threshold >= self.max_threshold:
            raise ValueError(f"min_threshold必须小于max_threshold")

        if not 0 <= self.percentile <= 100:
            raise ValueError(f"percentile必须在[0, 100]之间: {self.percentile}")

        if self.std_multiplier <= 0:
            raise ValueError(f"std_multiplier必须大于0: {self.std_multiplier}")

        if self.min_results < 0:
            raise ValueError(f"min_results必须大于等于0: {self.min_results}")


class DynamicThresholdFilter:
    """
    动态阈值过滤器

    功能：
    - 基于结果分布自适应计算阈值
    - 多种阈值计算方法
    - 自动参数调整
    - 统计信息收集

    参数：
        config: 阈值配置
    """

    def __init__(
        self,
        config: Optional[ThresholdConfig] = None,
    ):
        """
        初始化动态阈值过滤器

        Args:
            config: 阈值配置
        """
        self.config = config or ThresholdConfig()

        # 统计信息
        self._total_filtered = 0
        self._total_processed = 0
        self._threshold_history: List[float] = []

        logger.info(
            f"DynamicThresholdFilter 初始化: "
            f"method={self.config.method.value}, "
            f"min_threshold={self.config.min_threshold}"
        )

    def filter(
        self,
        results: List[RetrievalResult],
        return_threshold: bool = False,
    ) -> Union[List[RetrievalResult], Tuple[List[RetrievalResult], float]]:
        """
        过滤检索结果

        Args:
            results: 检索结果列表
            return_threshold: 是否返回使用的阈值

        Returns:
            过滤后的结果列表，或 (结果列表, 阈值) 元组
        """
        if not results:
            logger.debug("结果列表为空，无需过滤")
            return ([], 0.0) if return_threshold else []

        self._total_processed += len(results)

        # 提取分数
        scores = np.array([r.score for r in results])

        # 计算阈值
        threshold = self._compute_threshold(scores, results)

        # 记录阈值
        self._threshold_history.append(threshold)

        # 应用阈值过滤
        filtered_results = [
            r for r in results
            if r.score >= threshold
        ]

        # 确保至少保留min_results个结果
        if len(filtered_results) < self.config.min_results:
            # 按分数排序，取前min_results个
            sorted_results = sorted(results, key=lambda x: x.score, reverse=True)
            filtered_results = sorted_results[:self.config.min_results]
            threshold = filtered_results[-1].score if filtered_results else 0.0

        self._total_filtered += len(results) - len(filtered_results)

        logger.info(
            f"过滤完成: {len(results)} -> {len(filtered_results)} "
            f"(threshold={threshold:.3f})"
        )

        if return_threshold:
            return filtered_results, threshold
        return filtered_results

    def _compute_threshold(
        self,
        scores: np.ndarray,
        results: List[RetrievalResult],
    ) -> float:
        """
        计算阈值

        Args:
            scores: 分数数组
            results: 检索结果列表

        Returns:
            阈值
        """
        if self.config.method == ThresholdMethod.PERCENTILE:
            threshold = self._percentile_threshold(scores)
        elif self.config.method == ThresholdMethod.STD_DEV:
            threshold = self._std_dev_threshold(scores)
        elif self.config.method == ThresholdMethod.GAP_DETECTION:
            threshold = self._gap_detection_threshold(scores)
        else:  # ADAPTIVE
            # 自适应方法：综合多种方法
            thresholds = [
                self._percentile_threshold(scores),
                self._std_dev_threshold(scores),
                self._gap_detection_threshold(scores),
            ]
            # 使用中位数作为最终阈值
            threshold = float(np.median(thresholds))

        # 限制在[min_threshold, max_threshold]范围内
        threshold = np.clip(
            threshold,
            self.config.min_threshold,
            self.config.max_threshold,
        )

        # 自动调整
        if self.config.enable_auto_adjust:
            threshold = self._auto_adjust_threshold(threshold, scores)

        return float(threshold)

    def _percentile_threshold(self, scores: np.ndarray) -> float:
        """
        基于百分位数计算阈值

        Args:
            scores: 分数数组

        Returns:
            阈值
        """
        percentile = self.config.percentile
        threshold = float(np.percentile(scores, percentile))

        logger.debug(f"百分位数阈值: {threshold:.3f} (percentile={percentile})")
        return threshold

    def _std_dev_threshold(self, scores: np.ndarray) -> float:
        """
        基于标准差计算阈值

        threshold = mean - std_multiplier * std

        Args:
            scores: 分数数组

        Returns:
            阈值
        """
        mean = float(np.mean(scores))
        std = float(np.std(scores))
        multiplier = self.config.std_multiplier

        threshold = mean - multiplier * std

        logger.debug(f"标准差阈值: {threshold:.3f} (mean={mean:.3f}, std={std:.3f})")
        return threshold

    def _gap_detection_threshold(self, scores: np.ndarray) -> float:
        """
        基于跳变检测计算阈值

        找到分数分布中最大的"跳变"位置，以此为阈值

        Args:
            scores: 分数数组（降序排列）

        Returns:
            阈值
        """
        # 降序排列
        sorted_scores = np.sort(scores)[::-1]

        if len(sorted_scores) < 2:
            return float(sorted_scores[0]) if len(sorted_scores) > 0 else 0.0

        # 计算相邻分数的差值
        gaps = np.diff(sorted_scores)

        # 找到最大的跳变位置
        max_gap_idx = int(np.argmax(gaps))

        # 阈值为跳变后的分数
        threshold = float(sorted_scores[max_gap_idx + 1])

        logger.debug(
            f"跳变检测阈值: {threshold:.3f} "
            f"(gap={gaps[max_gap_idx]:.3f}, idx={max_gap_idx})"
        )
        return threshold

    def _auto_adjust_threshold(
        self,
        threshold: float,
        scores: np.ndarray,
    ) -> float:
        """
        自动调整阈值

        基于历史阈值和当前分数分布调整

        Args:
            threshold: 当前阈值
            scores: 分数数组

        Returns:
            调整后的阈值
        """
        if not self._threshold_history:
            return threshold

        # 计算历史阈值的移动平均
        recent_thresholds = self._threshold_history[-10:]  # 最近10次
        avg_threshold = float(np.mean(recent_thresholds))

        # 当前阈值与历史平均的差异
        diff = threshold - avg_threshold

        # 如果差异过大（>0.2），向历史平均靠拢
        if abs(diff) > 0.2:
            adjusted_threshold = avg_threshold + diff * 0.5  # 向中间靠拢50%
            logger.debug(
                f"阈值调整: {threshold:.3f} -> {adjusted_threshold:.3f} "
                f"(历史平均={avg_threshold:.3f})"
            )
            return adjusted_threshold

        return threshold

    def filter_by_confidence(
        self,
        results: List[RetrievalResult],
        min_confidence: float = 0.5,
    ) -> List[RetrievalResult]:
        """
        基于置信度过滤结果

        Args:
            results: 检索结果列表
            min_confidence: 最小置信度

        Returns:
            过滤后的结果列表
        """
        filtered = []
        for result in results:
            # 对于关系结果，使用confidence字段
            if result.result_type == "relation":
                confidence = result.metadata.get("confidence", 1.0)
                if confidence >= min_confidence:
                    filtered.append(result)
            else:
                # 对于段落结果，直接使用分数
                if result.score >= min_confidence:
                    filtered.append(result)

        logger.info(
            f"置信度过滤: {len(results)} -> {len(filtered)} "
            f"(min_confidence={min_confidence})"
        )

        return filtered

    def filter_by_diversity(
        self,
        results: List[RetrievalResult],
        similarity_threshold: float = 0.9,
        top_k: int = 10,
    ) -> List[RetrievalResult]:
        """
        基于多样性过滤结果（去除重复）

        Args:
            results: 检索结果列表
            similarity_threshold: 相似度阈值（高于此值视为重复）
            top_k: 最多保留结果数

        Returns:
            过滤后的结果列表
        """
        if not results:
            return []

        # 按分数排序
        sorted_results = sorted(results, key=lambda x: x.score, reverse=True)

        # 贪心选择：选择与已选结果相似度低的结果
        selected = []
        selected_hashes = []

        for result in sorted_results:
            if len(selected) >= top_k:
                break

            # 检查与已选结果的相似度
            is_duplicate = False
            for selected_hash in selected_hashes:
                # 简单判断：基于hash的前缀
                if result.hash_value[:8] == selected_hash[:8]:
                    is_duplicate = True
                    break

            if not is_duplicate:
                selected.append(result)
                selected_hashes.append(result.hash_value)

        logger.info(
            f"多样性过滤: {len(results)} -> {len(selected)} "
            f"(similarity_threshold={similarity_threshold})"
        )

        return selected

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        filter_rate = (
            self._total_filtered / self._total_processed
            if self._total_processed > 0
            else 0.0
        )

        stats = {
            "config": {
                "method": self.config.method.value,
                "min_threshold": self.config.min_threshold,
                "max_threshold": self.config.max_threshold,
                "percentile": self.config.percentile,
                "std_multiplier": self.config.std_multiplier,
                "min_results": self.config.min_results,
                "enable_auto_adjust": self.config.enable_auto_adjust,
            },
            "statistics": {
                "total_processed": self._total_processed,
                "total_filtered": self._total_filtered,
                "filter_rate": filter_rate,
                "avg_threshold": float(np.mean(self._threshold_history))
                if self._threshold_history else 0.0,
                "threshold_count": len(self._threshold_history),
            },
        }

        if self._threshold_history:
            stats["statistics"]["min_threshold_used"] = float(np.min(self._threshold_history))
            stats["statistics"]["max_threshold_used"] = float(np.max(self._threshold_history))

        return stats

    def reset_statistics(self) -> None:
        """重置统计信息"""
        self._total_filtered = 0
        self._total_processed = 0
        self._threshold_history.clear()
        logger.info("统计信息已重置")

    def __repr__(self) -> str:
        return (
            f"DynamicThresholdFilter("
            f"method={self.config.method.value}, "
            f"min_threshold={self.config.min_threshold}, "
            f"filtered={self._total_filtered}/{self._total_processed})"
        )

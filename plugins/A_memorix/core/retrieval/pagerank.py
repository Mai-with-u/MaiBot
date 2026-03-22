"""
Personalized PageRank实现

提供个性化的图节点排序功能。
"""

from typing import Dict, List, Optional, Tuple, Union, Any
from dataclasses import dataclass
import numpy as np

from src.common.logger import get_logger
from ..storage import GraphStore
from ..utils.matcher import AhoCorasick

logger = get_logger("A_Memorix.PersonalizedPageRank")


@dataclass
class PageRankConfig:
    """
    PageRank配置

    属性：
        alpha: 阻尼系数（0-1之间）
        max_iter: 最大迭代次数
        tol: 收敛阈值
        normalize: 是否归一化结果
        min_iterations: 最小迭代次数
    """

    alpha: float = 0.85
    max_iter: int = 100
    tol: float = 1e-6
    normalize: bool = True
    min_iterations: int = 20

    def __post_init__(self):
        """验证配置"""
        if not 0 <= self.alpha < 1:
            raise ValueError(f"alpha必须在[0, 1)之间: {self.alpha}")

        if self.max_iter <= 0:
            raise ValueError(f"max_iter必须大于0: {self.max_iter}")

        if self.tol <= 0:
            raise ValueError(f"tol必须大于0: {self.tol}")

        if self.min_iterations < 0:
            raise ValueError(f"min_iterations必须大于等于0: {self.min_iterations}")

        if self.min_iterations >= self.max_iter:
            raise ValueError(f"min_iterations必须小于max_iter")


class PersonalizedPageRank:
    """
    Personalized PageRank计算器

    功能：
    - 个性化向量支持
    - 快速收敛检测
    - 结果归一化
    - 批量计算
    - 统计信息

    参数：
        graph_store: 图存储
        config: PageRank配置
    """

    def __init__(
        self,
        graph_store: GraphStore,
        config: Optional[PageRankConfig] = None,
    ):
        """
        初始化PPR计算器

        Args:
            graph_store: 图存储
            config: PageRank配置
        """
        self.graph_store = graph_store
        self.config = config or PageRankConfig()

        # 统计信息
        self._total_computations = 0
        self._total_iterations = 0
        self._convergence_history: List[int] = []

        logger.info(
            f"PersonalizedPageRank 初始化: "
            f"alpha={self.config.alpha}, "
            f"max_iter={self.config.max_iter}"
        )

        # 缓存 Aho-Corasick 匹配器
        self._ac_matcher: Optional[AhoCorasick] = None
        self._ac_nodes_count = 0

    def compute(
        self,
        personalization: Optional[Dict[str, float]] = None,
        alpha: Optional[float] = None,
        max_iter: Optional[int] = None,
        normalize: Optional[bool] = None,
    ) -> Dict[str, float]:
        """
        计算Personalized PageRank

        Args:
            personalization: 个性化向量 {节点名: 权重}
            alpha: 阻尼系数（覆盖配置值）
            max_iter: 最大迭代次数（覆盖配置值）
            normalize: 是否归一化（覆盖配置值）

        Returns:
            节点PageRank值字典 {节点名: 分数}
        """
        # 使用覆盖值或配置值
        alpha = alpha if alpha is not None else self.config.alpha
        max_iter = max_iter if max_iter is not None else self.config.max_iter
        normalize = normalize if normalize is not None else self.config.normalize

        # 调用GraphStore的compute_pagerank
        scores = self.graph_store.compute_pagerank(
            personalization=personalization,
            alpha=alpha,
            max_iter=max_iter,
            tol=self.config.tol,
        )

        # 归一化（如果需要）
        if normalize and scores:
            total = sum(scores.values())
            if total > 0:
                scores = {node: score / total for node, score in scores.items()}

        # 更新统计
        self._total_computations += 1

        logger.debug(
            f"PPR计算完成: {len(scores)} 个节点, "
            f"personalization_nodes={len(personalization) if personalization else 0}"
        )

        return scores

    def compute_batch(
        self,
        personalization_list: List[Dict[str, float]],
        normalize: bool = True,
    ) -> List[Dict[str, float]]:
        """
        批量计算PPR

        Args:
            personalization_list: 个性化向量列表
            normalize: 是否归一化

        Returns:
            PageRank值字典列表
        """
        results = []

        for i, personalization in enumerate(personalization_list):
            logger.debug(f"计算第 {i+1}/{len(personalization_list)} 个PPR")
            scores = self.compute(personalization=personalization, normalize=normalize)
            results.append(scores)

        return results

    def compute_for_entities(
        self,
        entities: List[str],
        weights: Optional[List[float]] = None,
        normalize: bool = True,
    ) -> Dict[str, float]:
        """
        为实体列表计算PPR

        Args:
            entities: 实体列表
            weights: 权重列表（默认均匀权重）
            normalize: 是否归一化

        Returns:
            PageRank值字典
        """
        if not entities:
            logger.warning("实体列表为空，返回均匀PPR")
            return self.compute(personalization=None, normalize=normalize)

        # 构建个性化向量
        if weights is None:
            weights = [1.0] * len(entities)

        if len(weights) != len(entities):
            raise ValueError(f"权重数量与实体数量不匹配: {len(weights)} vs {len(entities)}")

        personalization = {entity: weight for entity, weight in zip(entities, weights)}

        return self.compute(personalization=personalization, normalize=normalize)

    def compute_for_query(
        self,
        query: str,
        entity_extractor: Optional[callable] = None,
        normalize: bool = True,
    ) -> Dict[str, float]:
        """
        为查询计算PPR

        Args:
            query: 查询文本
            entity_extractor: 实体提取函数（可选）
            normalize: 是否归一化

        Returns:
            PageRank值字典
        """
        # 提取实体
        if entity_extractor is not None:
            entities = entity_extractor(query)
        else:
            # 简单实现：基于图中的节点匹配
            entities = self._extract_entities_from_query(query)

        if not entities:
            logger.debug(f"未从查询中提取到实体: '{query}'")
            return self.compute(personalization=None, normalize=normalize)

        # 计算PPR
        return self.compute_for_entities(entities, normalize=normalize)

    def rank_nodes(
        self,
        scores: Dict[str, float],
        top_k: Optional[int] = None,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """
        对节点排序

        Args:
            scores: PageRank分数字典
            top_k: 返回前k个节点（None表示全部）
            min_score: 最小分数阈值

        Returns:
            排序后的节点列表 [(节点名, 分数), ...]
        """
        # 过滤低分节点
        filtered = [(node, score) for node, score in scores.items() if score >= min_score]

        # 按分数降序排序
        sorted_nodes = sorted(filtered, key=lambda x: x[1], reverse=True)

        # 返回top_k
        if top_k is not None:
            sorted_nodes = sorted_nodes[:top_k]

        return sorted_nodes

    def get_personalization_vector(
        self,
        nodes: List[str],
        method: str = "uniform",
    ) -> Dict[str, float]:
        """
        生成个性化向量

        Args:
            nodes: 节点列表
            method: 生成方法
                - "uniform": 均匀权重
                - "degree": 按度数加权
                - "inverse_degree": 按度数反比加权

        Returns:
            个性化向量 {节点名: 权重}
        """
        if not nodes:
            return {}

        if method == "uniform":
            # 均匀权重
            weight = 1.0 / len(nodes)
            return {node: weight for node in nodes}

        elif method == "degree":
            # 按度数加权
            node_degrees = {}
            for node in nodes:
                neighbors = self.graph_store.get_neighbors(node)
                node_degrees[node] = len(neighbors)

            total_degree = sum(node_degrees.values())
            if total_degree > 0:
                return {node: degree / total_degree for node, degree in node_degrees.items()}
            else:
                return {node: 1.0 / len(nodes) for node in nodes}

        elif method == "inverse_degree":
            # 按度数反比加权
            node_degrees = {}
            for node in nodes:
                neighbors = self.graph_store.get_neighbors(node)
                node_degrees[node] = len(neighbors)

            # 反度数
            inv_degrees = {node: 1.0 / (degree + 1) for node, degree in node_degrees.items()}
            total_inv = sum(inv_degrees.values())

            if total_inv > 0:
                return {node: inv / total_inv for node, inv in inv_degrees.items()}
            else:
                return {node: 1.0 / len(nodes) for node in nodes}

        else:
            raise ValueError(f"不支持的个性化向量生成方法: {method}")

    def compare_scores(
        self,
        scores1: Dict[str, float],
        scores2: Dict[str, float],
    ) -> Dict[str, Dict[str, float]]:
        """
        比较两组PPR分数

        Args:
            scores1: 第一组分数
            scores2: 第二组分数

        Returns:
            比较结果 {
                "common_nodes": {节点: (score1, score2)},
                "only_in_1": {节点: score1},
                "only_in_2": {节点: score2},
            }
        """
        common_nodes = {}
        only_in_1 = {}
        only_in_2 = {}

        all_nodes = set(scores1.keys()) | set(scores2.keys())

        for node in all_nodes:
            if node in scores1 and node in scores2:
                common_nodes[node] = (scores1[node], scores2[node])
            elif node in scores1:
                only_in_1[node] = scores1[node]
            else:
                only_in_2[node] = scores2[node]

        return {
            "common_nodes": common_nodes,
            "only_in_1": only_in_1,
            "only_in_2": only_in_2,
        }

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        avg_iterations = (
            self._total_iterations / self._total_computations
            if self._total_computations > 0
            else 0
        )

        return {
            "config": {
                "alpha": self.config.alpha,
                "max_iter": self.config.max_iter,
                "tol": self.config.tol,
                "normalize": self.config.normalize,
                "min_iterations": self.config.min_iterations,
            },
            "statistics": {
                "total_computations": self._total_computations,
                "total_iterations": self._total_iterations,
                "avg_iterations": avg_iterations,
                "convergence_history": self._convergence_history.copy(),
            },
            "graph": {
                "num_nodes": self.graph_store.num_nodes,
                "num_edges": self.graph_store.num_edges,
            },
        }

    def reset_statistics(self) -> None:
        """重置统计信息"""
        self._total_computations = 0
        self._total_iterations = 0
        self._convergence_history.clear()
        logger.info("统计信息已重置")

    def _extract_entities_from_query(self, query: str) -> List[str]:
        """
        从查询中提取实体（简化实现）

        Args:
            query: 查询文本

        Returns:
            实体列表
        """
        # 获取所有节点
        all_nodes = self.graph_store.get_nodes()
        if not all_nodes:
            return []

        # 检查是否需要更新 Aho-Corasick 匹配器
        if self._ac_matcher is None or self._ac_nodes_count != len(all_nodes):
            self._ac_matcher = AhoCorasick()
            for node in all_nodes:
                # 统一转为小写进行不区分大小写匹配
                self._ac_matcher.add_pattern(node.lower())
            self._ac_matcher.build()
            self._ac_nodes_count = len(all_nodes)

        # 执行匹配
        query_lower = query.lower()
        stats = self._ac_matcher.find_all(query_lower)
        
        # 转换回原始的大小写（这里简化为从 all_nodes 中找，或者 AC 存原始值）
        # 为了简单，AC 中 add_pattern 存的是小写
        # 我们需要一个映射：小写 -> 原始
        node_map = {node.lower(): node for node in all_nodes}
        entities = [node_map[low_name] for low_name in stats.keys()]

        return entities

    @property
    def num_computations(self) -> int:
        """计算次数"""
        return self._total_computations

    @property
    def avg_iterations(self) -> float:
        """平均迭代次数"""
        if self._total_computations == 0:
            return 0.0
        return self._total_iterations / self._total_computations

    def __repr__(self) -> str:
        return (
            f"PersonalizedPageRank("
            f"alpha={self.config.alpha}, "
            f"computations={self._total_computations})"
        )


def create_ppr_from_graph(
    graph_store: GraphStore,
    alpha: float = 0.85,
    max_iter: int = 100,
) -> PersonalizedPageRank:
    """
    从图存储创建PPR计算器

    Args:
        graph_store: 图存储
        alpha: 阻尼系数
        max_iter: 最大迭代次数

    Returns:
        PPR计算器实例
    """
    config = PageRankConfig(
        alpha=alpha,
        max_iter=max_iter,
    )

    return PersonalizedPageRank(
        graph_store=graph_store,
        config=config,
    )

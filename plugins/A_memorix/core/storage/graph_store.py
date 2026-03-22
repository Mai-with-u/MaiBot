"""
图存储模块

基于SciPy稀疏矩阵的知识图谱存储与计算。
"""

import pickle
from enum import Enum
from pathlib import Path
from typing import Optional, Union, Tuple, List, Dict, Set, Any
from collections import defaultdict
import threading
import asyncio

import numpy as np

class SparseMatrixFormat(Enum):
    """稀疏矩阵格式"""
    CSR = "csr"
    CSC = "csc"

try:
    from scipy.sparse import csr_matrix, csc_matrix, triu, save_npz, load_npz, bmat, lil_matrix
    from scipy.sparse.linalg import norm
    HAS_SCIPY = True
except ImportError:
    class _SparseMatrixPlaceholder:
        pass

    def _scipy_missing(*args, **kwargs):
        raise ImportError("SciPy 未安装，请安装: pip install scipy")

    csr_matrix = _SparseMatrixPlaceholder
    csc_matrix = _SparseMatrixPlaceholder
    lil_matrix = _SparseMatrixPlaceholder
    triu = _scipy_missing
    save_npz = _scipy_missing
    load_npz = _scipy_missing
    bmat = _scipy_missing
    norm = _scipy_missing
    HAS_SCIPY = False

import contextlib
from src.common.logger import get_logger
from ..utils.hash import compute_hash
from ..utils.io import atomic_write

logger = get_logger("A_Memorix.GraphStore")


class GraphModificationMode(Enum):
    """图修改模式"""
    BATCH = "batch"             # 批量模式 (默认, 适合一次性加载)
    INCREMENTAL = "incremental" # 增量模式 (适合频繁随机写入, 使用LIL)
    READ_ONLY = "read_only"     # 只读模式 (适合计算, CSR/CSC)


class GraphStore:
    """
    图存储类

    功能：
    - CSR稀疏矩阵存储图结构
    - 节点和边的CRUD操作
    - Personalized PageRank计算
    - 同义词自动连接
    - 图持久化

    参数：
        matrix_format: 稀疏矩阵格式（csr/csc）
        data_dir: 数据目录
    """

    def __init__(
        self,
        matrix_format: str = "csr",
        data_dir: Optional[Union[str, Path]] = None,
    ):
        """
        初始化图存储

        Args:
            matrix_format: 稀疏矩阵格式（csr/csc）
            data_dir: 数据目录
        """
        if not HAS_SCIPY:
            raise ImportError("SciPy 未安装，请安装: pip install scipy")

        if isinstance(matrix_format, SparseMatrixFormat):
            self.matrix_format = matrix_format.value
        else:
            self.matrix_format = str(matrix_format).lower()
        self.data_dir = Path(data_dir) if data_dir else None

        # 节点管理
        self._nodes: List[str] = []  # 节点列表
        self._node_to_idx: Dict[str, int] = {}  # 节点名到索引的映射
        self._node_attrs: Dict[str, Dict[str, Any]] = {}  # 节点属性

        # 边管理（邻接矩阵）
        self._adjacency: Optional[Union[csr_matrix, csc_matrix]] = None

        # 统计信息
        self._total_nodes_added = 0
        self._total_edges_added = 0
        self._total_nodes_deleted = 0
        self._total_edges_deleted = 0
        
        # 状态管理
        self._modification_mode = GraphModificationMode.BATCH
        
        # 状态管理
        self._adjacency_T: Optional[Union[csr_matrix, csc_matrix]] = None
        self._adjacency_dirty: bool = True
        self._saliency_cache: Optional[Dict[str, float]] = None

        # V5: 多关系映射 (src_idx, dst_idx) -> Set[relation_hash]
        self._edge_hash_map: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
        # V5: 简单的异步锁 (实际上 asyncio 环境下单线程主循环可能不需要，但为了安全保留)
        self._lock = asyncio.Lock()

        logger.info(f"GraphStore 初始化: format={matrix_format}")

    def _canonicalize(self, node: str) -> str:
        """规范化节点名称 (用于去重和内部索引)"""
        if not node:
            return ""
        return str(node).strip().lower()

    @contextlib.contextmanager
    def batch_update(self):
        """
        批量更新上下文管理器
        
        进入时切换到 LIL 格式以优化随机/增量更新
        退出时恢复到 CSR/CSC 格式以优化存储和计算
        """
        original_mode = self._modification_mode
        self._switch_mode(GraphModificationMode.INCREMENTAL)
        try:
            yield
        finally:
            self._switch_mode(original_mode)
            
    def _switch_mode(self, new_mode: GraphModificationMode):
        """切换修改模式并转换矩阵格式"""
        if new_mode == self._modification_mode:
            return
            
        if self._adjacency is None:
            self._modification_mode = new_mode
            return

        logger.debug(f"切换图模式: {self._modification_mode.value} -> {new_mode.value}")
        
        # 转换逻辑
        if new_mode == GraphModificationMode.INCREMENTAL:
            # 转换为 LIL 格式
            if not isinstance(self._adjacency, lil_matrix): # 粗略检查是否非 lil
                 try:
                     self._adjacency = self._adjacency.tolil()
                     logger.debug("已转换为 LIL 格式")
                 except Exception as e:
                     logger.warning(f"转换为 LIL 失败: {e}")
        
        elif new_mode in [GraphModificationMode.BATCH, GraphModificationMode.READ_ONLY]:
            # 转换回配置的格式 (CSR/CSC)
            if self.matrix_format == "csr":
                self._adjacency = self._adjacency.tocsr()
            elif self.matrix_format == "csc":
                self._adjacency = self._adjacency.tocsc()
            logger.debug(f"已恢复为 {self.matrix_format.upper()} 格式")
            
        self._modification_mode = new_mode

    def add_nodes(
        self,
        nodes: List[str],
        attributes: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> int:
        """
        添加节点

        Args:
            nodes: 节点名称列表
            attributes: 节点属性字典 {node_name: {attr: value}}

        Returns:
            成功添加的节点数量
        """
        added = 0
        for node in nodes:
            canon = self._canonicalize(node)
            if canon in self._node_to_idx:
                logger.debug(f"节点已存在，跳过: {node}")
                continue

            # 添加到节点列表
            idx = len(self._nodes)
            self._nodes.append(node) # 存储原始节点名
            self._node_to_idx[canon] = idx # 映射规范化节点名到索引
            self._adjacency_dirty = True
            self._saliency_cache = None

            # 添加属性
            if attributes and node in attributes:
                self._node_attrs[canon] = attributes[node]
            else:
                self._node_attrs[canon] = {}

            added += 1
            self._total_nodes_added += 1

        # 扩展邻接矩阵
        if added > 0:
            self._expand_adjacency_matrix(added)

        logger.debug(f"添加 {added} 个节点")
        return added

    def add_edges(
        self,
        edges: List[Tuple[str, str]],
        weights: Optional[List[float]] = None,
        relation_hashes: Optional[List[str]] = None, # V5: 支持关系哈希映射 (Relation Hash Mapping)
    ) -> int:
        """
        添加边

        Args:
            edges: 边列表 [(source, target), ...]
            weights: 边权重列表（默认为1.0）

        Returns:
            成功添加的边数量
        """
        if not edges:
            return 0

        # 确保所有节点存在
        nodes_to_add = set()
        for src, tgt in edges:
            src_canon = self._canonicalize(src)
            tgt_canon = self._canonicalize(tgt)
            if src_canon not in self._node_to_idx:
                nodes_to_add.add(src)
            if tgt_canon not in self._node_to_idx:
                nodes_to_add.add(tgt)

        if nodes_to_add:
            self.add_nodes(list(nodes_to_add))

        # 处理权重
        if weights is None:
            weights = [1.0] * len(edges)

        if len(weights) != len(edges):
            raise ValueError(f"边数量与权重数量不匹配: {len(edges)} vs {len(weights)}")

        # 如果仅仅是添加边且处于增量模式 (LIL)，直接更新
        if self._modification_mode == GraphModificationMode.INCREMENTAL:
             if self._adjacency is None:
                 # 初始化为空 LIL
                 n = len(self._nodes)
                 from scipy.sparse import lil_matrix
                 self._adjacency = lil_matrix((n, n), dtype=np.float32)

             # 尝试直接使用 LIL 索引更新
             try:
                 # 批量获取索引
                 rows = [self._node_to_idx[self._canonicalize(src)] for src, _ in edges]
                 cols = [self._node_to_idx[self._canonicalize(tgt)] for _, tgt in edges]
                 
                 # 确保矩阵足够大 (如果 add_nodes 没有扩展它) - 通常 add_nodes 会处理
                 # 这里直接赋值
                 self._adjacency[rows, cols] = weights
                 
                 self._total_edges_added += len(edges)
                 
                 # V5: Update edge hash map
                 if relation_hashes:
                     for (src, tgt), r_hash in zip(edges, relation_hashes):
                         if r_hash:
                             s_idx = self._node_to_idx[self._canonicalize(src)]
                             t_idx = self._node_to_idx[self._canonicalize(tgt)]
                             self._edge_hash_map[(s_idx, t_idx)].add(r_hash)

                 logger.debug(f"增量添加 {len(edges)} 条边 (LIL)")
                 return len(edges)
             except Exception as e:
                 logger.warning(f"LIL 增量更新失败，回退到通用方法: {e}")
                 # Fallback to general method below

        # 通用方法 (构建 COO 然后合并)
        # 构建边的三元组
        row_indices = []
        col_indices = []
        data_values = []

        for (src, tgt), weight in zip(edges, weights):
            src_idx = self._node_to_idx[self._canonicalize(src)]
            tgt_idx = self._node_to_idx[self._canonicalize(tgt)]

            row_indices.append(src_idx)
            col_indices.append(tgt_idx)
            data_values.append(weight)

        # 创建新的边的矩阵
        n = len(self._nodes)
        new_edges = csr_matrix(
            (data_values, (row_indices, col_indices)),
            shape=(n, n),
        )

        # 合并到邻接矩阵
        if self._adjacency is None:
            self._adjacency = new_edges
        else:
            self._adjacency = self._adjacency + new_edges

        # 转换为指定格式
        if self.matrix_format == "csc" and isinstance(self._adjacency, csr_matrix):
            self._adjacency = self._adjacency.tocsc()
        elif self.matrix_format == "csr" and isinstance(self._adjacency, csc_matrix):
            self._adjacency = self._adjacency.tocsr()

        self._total_edges_added += len(edges)
        self._adjacency_dirty = True  # 标记脏位
        
        # V5: 更新边哈希映射 (Edge Hash Map)
        if relation_hashes:
            for (src, tgt), r_hash in zip(edges, relation_hashes):
                if r_hash:
                    try:
                        s_idx = self._node_to_idx[self._canonicalize(src)]
                        t_idx = self._node_to_idx[self._canonicalize(tgt)]
                        self._edge_hash_map[(s_idx, t_idx)].add(r_hash)
                    except KeyError:
                        pass # 正常情况下节点已在上方添加，此处仅作防错处理

        logger.debug(f"添加 {len(edges)} 条边")
        return len(edges)

    def update_edge_weight(
        self,
        source: str,
        target: str,
        delta: float,
        min_weight: float = 0.1,
        max_weight: float = 10.0,
    ) -> float:
        """
        更新边权重 (增量/强化/弱化)

        Args:
            source: 源节点
            target: 目标节点
            delta: 权重变化量 (+/-)
            min_weight: 最小权重限制
            max_weight: 最大权重限制

        Returns:
            更新后的权重
        """
        src_canon = self._canonicalize(source)
        tgt_canon = self._canonicalize(target)

        if src_canon not in self._node_to_idx or tgt_canon not in self._node_to_idx:
            logger.warning(f"节点不存在，无法更新权重: {source} -> {target}")
            return 0.0

        current_weight = self.get_edge_weight(source, target)
        if current_weight == 0.0 and delta <= 0:
            # 边不存在且试图减少权重，忽略
            return 0.0
        
        # 如果边不存在但 delta > 0，相当于添加新边 (默认基础权重0 + delta)
        # 但为了逻辑清晰，我们假设只更新存在的边，或者确实添加
        
        new_weight = current_weight + delta
        new_weight = max(min_weight, min(max_weight, new_weight))
        
        # 使用 batch_update 上下文自动处理格式转换
        # 这里我们临时切换到 incremental 模式进行单次更新
        with self.batch_update():
            # add_edges 会覆盖或添加，我们需要覆盖
            self.add_edges([(source, target)], [new_weight])
            
        logger.debug(f"更新权重 {source}->{target}: {current_weight:.2f} -> {new_weight:.2f}")
        return new_weight

    def delete_nodes(self, nodes: List[str]) -> int:
        """
        删除节点（及相关的边）

        Args:
            nodes: 要删除的节点列表

        Returns:
            成功删除的节点数量
        """
        if not nodes:
            return 0

        # 检查哪些节点存在
        existing_nodes = [node for node in nodes if self._canonicalize(node) in self._node_to_idx]
        if not existing_nodes:
            logger.warning("所有节点都不存在，无法删除")
            return 0

        # 获取要删除的索引
        indices_to_delete = {self._node_to_idx[self._canonicalize(node)] for node in existing_nodes}
        indices_to_keep = [
            i for i in range(len(self._nodes))
            if i not in indices_to_delete
        ]

        # 创建索引映射
        old_to_new = {old_idx: new_idx for new_idx, old_idx in enumerate(indices_to_keep)}

        # 重建节点列表 (存储原始节点名)
        self._nodes = [self._nodes[i] for i in indices_to_keep]
        # 重建规范化节点名到索引的映射
        self._node_to_idx = {self._canonicalize(self._nodes[new_idx]): new_idx for new_idx in range(len(self._nodes))}

        # 删除并重构节点属性
        new_node_attrs = {}
        for idx, node_name in enumerate(self._nodes):
            canon = self._canonicalize(node_name)
            if canon in self._node_attrs:
                new_node_attrs[canon] = self._node_attrs[canon]
        self._node_attrs = new_node_attrs

        # 重建邻接矩阵
        if self._adjacency is not None:
            # 转换为COO格式以进行切片，然后转换回原始格式
            self._adjacency = self._adjacency.tocoo()
            mask_rows = np.isin(self._adjacency.row, list(indices_to_keep))
            mask_cols = np.isin(self._adjacency.col, list(indices_to_keep))
            
            # 筛选出保留的行和列
            new_rows = self._adjacency.row[mask_rows & mask_cols]
            new_cols = self._adjacency.col[mask_rows & mask_cols]
            new_data = self._adjacency.data[mask_rows & mask_cols]

            # 更新索引
            new_rows = np.array([old_to_new[r] for r in new_rows])
            new_cols = np.array([old_to_new[c] for c in new_cols])

            n = len(self._nodes)
            if self.matrix_format == "csr":
                self._adjacency = csr_matrix((new_data, (new_rows, new_cols)), shape=(n, n))
            else: # csc
                self._adjacency = csc_matrix((new_data, (new_rows, new_cols)), shape=(n, n))

        # 重建关系哈希映射，移除涉及已删除节点的记录并重映射索引。
        if self._edge_hash_map:
            new_edge_hash_map: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
            for (old_src, old_tgt), hashes in self._edge_hash_map.items():
                if old_src in indices_to_delete or old_tgt in indices_to_delete:
                    continue
                if old_src in old_to_new and old_tgt in old_to_new and hashes:
                    new_edge_hash_map[(old_to_new[old_src], old_to_new[old_tgt])] = set(hashes)
            self._edge_hash_map = new_edge_hash_map

        deleted_count = len(existing_nodes)
        self._total_nodes_deleted += deleted_count
        self._adjacency_dirty = True
        self._saliency_cache = None

        logger.info(f"删除 {deleted_count} 个节点")
        return deleted_count

    def remove_nodes(self, nodes: List[str]) -> int:
        """兼容性别名：删除节点"""
        return self.delete_nodes(nodes)

    def delete_edges(
        self,
        edges: List[Tuple[str, str]],
    ) -> int:
        """
        删除边

        Args:
            edges: 要删除的边列表 [(source, target), ...]

        Returns:
            成功删除的边数量
        """
        if not edges:
            return 0

        deleted = 0
        # 构建要删除的边的索引集合
        edges_to_delete = set()
        for src, tgt in edges:
            src_canon = self._canonicalize(src)
            tgt_canon = self._canonicalize(tgt)
            if src_canon in self._node_to_idx and tgt_canon in self._node_to_idx:
                src_idx = self._node_to_idx[src_canon]
                tgt_idx = self._node_to_idx[tgt_canon]
                edges_to_delete.add((src_idx, tgt_idx))

        if self._adjacency is not None and edges_to_delete:
            # 转换为COO格式便于修改
            adj_coo = self._adjacency.tocoo()

            # 过滤要删除的边
            new_row = []
            new_col = []
            new_data = []

            for i, j, val in zip(adj_coo.row, adj_coo.col, adj_coo.data):
                if (i, j) not in edges_to_delete:
                    new_row.append(i)
                    new_col.append(j)
                    new_data.append(val)
                else:
                    deleted += 1

            # 重建邻接矩阵
            n = len(self._nodes)
            self._adjacency = csr_matrix((new_data, (new_row, new_col)), shape=(n, n))

            # 转换回指定格式
            if self.matrix_format == "csc":
                self._adjacency = self._adjacency.tocsc()

        # delete_edges 是“物理删除”语义，必须同步清理 edge_hash_map。
        if edges_to_delete and self._edge_hash_map:
            for key in edges_to_delete:
                self._edge_hash_map.pop(key, None)

        self._total_edges_deleted += deleted
        self._adjacency_dirty = True
        self._saliency_cache = None
        logger.info(f"删除 {deleted} 条边")
        return deleted

    def remove_edges(self, edges: List[Tuple[str, str]]) -> int:
        """兼容性别名：删除边"""
        return self.delete_edges(edges)

    def get_nodes(self) -> List[str]:
        """
        获取所有节点

        Returns:
            节点列表
        """
        return self._nodes.copy()

    def has_node(self, node: str) -> bool:
        """
        检查节点是否存在
        
        Args:
            node: 节点名称
        """
        return self._canonicalize(node) in self._node_to_idx

    def find_node(self, node: str, ignore_case: bool = False) -> Optional[str]:
        """
        查找节点 (由于底层已统一规范化，ignore_case 始终有效)
        
        Args:
            node: 节点名称
            ignore_case: 是否忽略大小写 (已默认忽略)
            
        Returns:
            真实节点名称 (如果存在)，否则 None
        """
        canon = self._canonicalize(node)
        if canon in self._node_to_idx:
            return self._nodes[self._node_to_idx[canon]]
        return None

    def get_node_attributes(self, node: str) -> Optional[Dict[str, Any]]:
        """
        获取节点属性

        Args:
            node: 节点名称

        Returns:
            节点属性字典，不存在则返回None
        """
        canon = self._canonicalize(node)
        return self._node_attrs.get(canon)

    def get_neighbors(self, node: str) -> List[str]:
        """
        获取节点的出邻居

        Args:
            node: 节点名称

        Returns:
            出邻居节点列表
        """
        canon = self._canonicalize(node)
        if canon not in self._node_to_idx or self._adjacency is None:
            return []

        idx = self._node_to_idx[canon]
        neighbor_indices = self._row_neighbor_indices(self._adjacency, idx)
        return [self._nodes[int(i)] for i in neighbor_indices]

    def get_in_neighbors(self, node: str) -> List[str]:
        """
        获取节点的入邻居

        Args:
            node: 节点名称

        Returns:
            入邻居节点列表
        """
        canon = self._canonicalize(node)
        if canon not in self._node_to_idx or self._adjacency is None:
            return []

        self._ensure_adjacency_T()
        if self._adjacency_T is None:
            return []

        idx = self._node_to_idx[canon]
        neighbor_indices = self._row_neighbor_indices(self._adjacency_T, idx)
        return [self._nodes[int(i)] for i in neighbor_indices]

    def get_edge_weight(self, source: str, target: str) -> float:
        """
        获取边的权重
        """
        src_canon = self._canonicalize(source)
        tgt_canon = self._canonicalize(target)

        if src_canon not in self._node_to_idx or tgt_canon not in self._node_to_idx:
            return 0.0

        if self._adjacency is None:
            return 0.0

        src_idx = self._node_to_idx[src_canon]
        tgt_idx = self._node_to_idx[tgt_canon]

        return float(self._adjacency[src_idx, tgt_idx])

    def canonicalize_node(self, node: str) -> str:
        """公开节点规范化接口，避免外部访问私有方法。"""
        return self._canonicalize(node)

    def has_edge_hash_map(self) -> bool:
        """是否存在 relation-hash 映射。"""
        return bool(self._edge_hash_map)

    def get_relation_hashes_for_edge(self, source: str, target: str) -> Set[str]:
        """获取边 (source -> target) 关联的关系哈希集合。"""
        src_canon = self._canonicalize(source)
        tgt_canon = self._canonicalize(target)
        if src_canon not in self._node_to_idx or tgt_canon not in self._node_to_idx:
            return set()
        src_idx = self._node_to_idx[src_canon]
        tgt_idx = self._node_to_idx[tgt_canon]
        return set(self._edge_hash_map.get((src_idx, tgt_idx), set()))

    def get_incident_relation_hashes(self, node: str, limit: Optional[int] = None) -> List[str]:
        """获取与指定节点关联的关系哈希列表（入边 + 出边）。"""
        canon = self._canonicalize(node)
        if canon not in self._node_to_idx or not self._edge_hash_map:
            return []

        idx = self._node_to_idx[canon]
        limit_val = max(1, int(limit)) if limit is not None else None
        collected: List[Tuple[str, str, str]] = []
        idx_to_node = self._nodes

        for (src_idx, tgt_idx), hashes in self._edge_hash_map.items():
            if idx not in {src_idx, tgt_idx}:
                continue
            src_name = idx_to_node[src_idx] if 0 <= src_idx < len(idx_to_node) else ""
            tgt_name = idx_to_node[tgt_idx] if 0 <= tgt_idx < len(idx_to_node) else ""
            for hash_value in hashes:
                hash_text = str(hash_value).strip()
                if not hash_text:
                    continue
                collected.append((src_name, tgt_name, hash_text))

        collected.sort(key=lambda x: (x[0].lower(), x[1].lower(), x[2]))
        out: List[str] = []
        seen = set()
        for _, _, hash_value in collected:
            if hash_value in seen:
                continue
            seen.add(hash_value)
            out.append(hash_value)
            if limit_val is not None and len(out) >= limit_val:
                break
        return out

    def edge_contains_relation_hash(self, source: str, target: str, hash_value: str) -> bool:
        """判断边是否包含指定关系哈希。"""
        if not hash_value:
            return False
        return str(hash_value) in self.get_relation_hashes_for_edge(source, target)

    def iter_edge_hash_entries(self) -> List[Tuple[str, str, Set[str]]]:
        """以节点名形式遍历 edge-hash-map。"""
        out: List[Tuple[str, str, Set[str]]] = []
        if not self._edge_hash_map:
            return out
        idx_to_node = self._nodes
        for (s_idx, t_idx), hashes in self._edge_hash_map.items():
            if not hashes:
                continue
            if s_idx < 0 or t_idx < 0:
                continue
            if s_idx >= len(idx_to_node) or t_idx >= len(idx_to_node):
                continue
            out.append((idx_to_node[s_idx], idx_to_node[t_idx], set(hashes)))
        return out

    def deactivate_edges(self, edges: List[Tuple[str, str]]) -> int:
        """
        冻结边 (将权重设为0.0，使其在计算意义上消失，但保留在Map中)
        
        Args:
            edges: [(s1, t1), (s2, t2)...]
        """
        if not edges or self._adjacency is None:
            return 0

        deactivated_count = 0
        with self.batch_update():
            # 我们需要 explicit set to 0.
            # 使用增量更新模式覆盖
            for s, t in edges:
                s_canon = self._canonicalize(s)
                t_canon = self._canonicalize(t)
                if s_canon in self._node_to_idx and t_canon in self._node_to_idx:
                    idx_s = self._node_to_idx[s_canon]
                    idx_t = self._node_to_idx[t_canon]
                    self._adjacency[idx_s, idx_t] = 0.0
                    deactivated_count += 1

        self._adjacency_dirty = True
        return deactivated_count

    def _ensure_adjacency_T(self):
        """确保转置邻接矩阵是最新的"""
        if self._adjacency is None:
            self._adjacency_T = None
            return

        if self._adjacency_dirty or self._adjacency_T is None:
            # 只有在确实需要时才计算转置
            # find_paths 以“按行读取邻居”为主，因此统一缓存为 CSR，避免
            # CSR->CSC 转置后按行切片读出错误的索引视图。
            self._adjacency_T = self._adjacency.transpose().tocsr()
            
            self._adjacency_dirty = False
            # logger.debug("重建转置邻接矩阵缓存")

    @staticmethod
    def _row_neighbor_indices(
        matrix: Optional[Union[csr_matrix, csc_matrix]],
        row_idx: int,
    ) -> np.ndarray:
        """返回指定行的非零列索引。"""
        if matrix is None:
            return np.asarray([], dtype=np.int32)

        if isinstance(matrix, csr_matrix):
            return matrix.indices[matrix.indptr[row_idx]:matrix.indptr[row_idx + 1]]

        row = matrix[row_idx, :]
        _, indices = row.nonzero()
        return np.asarray(indices, dtype=np.int32)

    def find_paths(
        self, 
        start_node: str, 
        end_node: str, 
        max_depth: int = 3, 
        max_paths: int = 5,
        max_expansions: int = 20000
    ) -> List[List[str]]:
        """
        查找两个节点之间的路径 (BFS)
        支持有向和无向 (视作双向) 探索
        
        Args:
            start_node: 起始节点
            end_node: 目标节点
            max_depth: 最大深度
            max_paths: 最大路径数 (找到这么多就停止)
            max_expansions: 最大扩展次数 (防止爆炸)
            
        Returns:
            路径列表 [[n1, n2, n3], ...]
        """
        start_canon = self._canonicalize(start_node)
        end_canon = self._canonicalize(end_node)

        if start_canon not in self._node_to_idx or end_canon not in self._node_to_idx:
            return []
            
        if self._adjacency is None:
            return []

        # 确保转置矩阵可用 (用于查找入边)
        self._ensure_adjacency_T()
        
        start_idx = self._node_to_idx[start_canon]
        end_idx = self._node_to_idx[end_canon]
        
        # 队列: (current_idx, path_indices)
        queue = [(start_idx, [start_idx])]
        found_paths = []
        expansions = 0
        
        unique_paths = set()
        
        while queue:
            curr, path = queue.pop(0)
            
            if len(path) > max_depth + 1:
                continue
                
            if curr == end_idx:
                # 找到路径
                # 转换回节点名
                path_names = [self._nodes[i] for i in path]
                path_tuple = tuple(path_names)
                if path_tuple not in unique_paths:
                    found_paths.append(path_names)
                    unique_paths.add(path_tuple)
                    
                if len(found_paths) >= max_paths:
                    break
                continue
                
            if expansions >= max_expansions:
                break
            
            expansions += 1
            
            # 获取邻居 (出边 + 入边)
            out_indices = self._row_neighbor_indices(self._adjacency, curr)
            
            # 2. 入边 (使用转置矩阵)
            if self._adjacency_T is not None:
                in_indices = self._row_neighbor_indices(self._adjacency_T, curr)
                neighbors = np.concatenate((out_indices, in_indices))
            else:
                neighbors = out_indices
                
            # 去重并过滤已在路径中的节点 (防止环)
            # 注意: 这里简单去重，可能包含重复的邻居(如果既是出又是入)
            seen_in_path = set(path)
            queued_neighbors = set()
            
            for neighbor_idx in neighbors:
                neighbor = int(neighbor_idx)
                if neighbor not in seen_in_path and neighbor not in queued_neighbors:
                    # 只有未访问过的才加入
                    queue.append((neighbor, path + [neighbor]))
                    queued_neighbors.add(neighbor)
                    
        return found_paths

    def compute_pagerank(
        self,
        personalization: Optional[Dict[str, float]] = None,
        alpha: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> Dict[str, float]:
        """
        计算Personalized PageRank

        Args:
            personalization: 个性化向量 {node: weight}，默认为均匀分布
            alpha: 阻尼系数（0-1之间）
            max_iter: 最大迭代次数
            tol: 收敛阈值

        Returns:
            节点PageRank值字典 {node: score}
        """
        if self._adjacency is None or len(self._nodes) == 0:
            logger.warning("图为空，无法计算PageRank")
            return {}

        n = len(self._nodes)

        # 构建列归一化的转移矩阵
        adj = self._adjacency.astype(np.float32)

        # 计算出度
        out_degrees = np.array(adj.sum(axis=1)).flatten()

        # 处理悬挂节点（出度为0）
        dangling = (out_degrees == 0)
        out_degrees_inv = np.zeros_like(out_degrees)
        out_degrees_inv[~dangling] = 1.0 / out_degrees[~dangling]

        # 归一化 (使用稀疏对角阵避免内存溢出)
        from scipy.sparse import diags
        D_inv = diags(out_degrees_inv)
        M = adj.T @ D_inv  # 转移矩阵

        # 初始化个性化向量
        if personalization is None:
            # 均匀分布
            p = np.ones(n) / n
        else:
            # 使用指定的个性化向量
            p = np.zeros(n)
            total_weight = sum(personalization.values())
            for node, weight in personalization.items():
                if node in self._node_to_idx:
                    idx = self._node_to_idx[node]
                    p[idx] = weight / total_weight

            # 确保和为1
            if p.sum() == 0:
                p = np.ones(n) / n
            else:
                p = p / p.sum()

        # 幂迭代法
        p_orig = p.copy()
        for i in range(max_iter):
            # p_new = alpha * M * p + (1-alpha) * personalization
            p_new = alpha * (M @ p) + (1 - alpha) * p_orig
            
            # 处理因为悬挂节点导致的概率流失
            current_sum = p_new.sum()
            if current_sum < 1.0:
                p_new += (1.0 - current_sum) * p_orig

            # 检查收敛
            diff = np.linalg.norm(p_new - p, 1)
            if diff < tol:
                logger.debug(f"PageRank在 {i+1} 次迭代后收敛")
                p = p_new
                break
            p = p_new
        else:
            logger.warning(f"PageRank未在 {max_iter} 次迭代内收敛")

        # 转换为真实节点名称字典
        return {self._nodes[idx]: float(val) for idx, val in enumerate(p)}

    def get_saliency_scores(self) -> Dict[str, float]:
        """
        获取节点显著性得分 (带有缓存机制)
        """
        if self._saliency_cache is not None and not self._adjacency_dirty:
            return self._saliency_cache

        logger.debug("正在计算节点显著性得分 (PageRank)...")
        scores = self.compute_pagerank()
        self._saliency_cache = scores
        # 注意：这里我们不把 _adjacency_dirty 设为 False，因为其它逻辑(如_adjacency_T)也依赖它
        return scores

    def connect_synonyms(
        self,
        similarity_matrix: np.ndarray,
        node_list: List[str],
        threshold: float = 0.85,
    ) -> int:
        """
        连接相似节点（同义词）

        Args:
            similarity_matrix: 相似度矩阵 (N x N)
            node_list: 对应的节点列表（长度为N）
            threshold: 相似度阈值

        Returns:
            添加的边数量
        """
        if len(node_list) != similarity_matrix.shape[0]:
            raise ValueError(
                f"节点列表长度与相似度矩阵维度不匹配: "
                f"{len(node_list)} vs {similarity_matrix.shape[0]}"
            )

        # 找到相似的节点对（上三角，排除对角线）
        similar_pairs = np.argwhere(
            (triu(similarity_matrix, k=1) >= threshold) &
            (triu(similarity_matrix, k=1) < 1.0)  # 排除完全相同的
        )

        # 添加边
        edges = []
        for i, j in similar_pairs:
            if i < len(node_list) and j < len(node_list):
                src = node_list[i]
                tgt = node_list[j]
                # 使用相似度作为权重
                weight = float(similarity_matrix[i, j])
                edges.append((src, tgt, weight))

        if edges:
            edge_pairs = [(src, tgt) for src, tgt, _ in edges]
            weights = [w for _, _, w in edges]
            count = self.add_edges(edge_pairs, weights)
            logger.info(f"连接 {count} 对相似节点（阈值={threshold}）")
            return count
        return 0


    # =========================================================================
    # V5 Memory System Methods (Graph Level)
    # =========================================================================

    def decay(self, factor: float, min_active_weight: float = 0.0) -> None:
        """
        全图衰减 (Atomic Decay)
        
        Args:
            factor: 衰减因子 (0.0 < factor < 1.0)
            min_active_weight: 最小活跃权重 (低于此值可能被视为无效，但在物理修剪前仍保留)
        """
        if self._adjacency is None or factor >= 1.0 or factor <= 0.0:
            return
            
        logger.debug(f"正在执行全图衰减，因子: {factor}")
        
        # 直接矩阵乘法，SciPy CSR/CSC 非常高效
        self._adjacency *= factor
        
        # 如果需要处理极小值 (可选，防止下溢，但通常浮点数足够小)
        # if min_active_weight > 0:
        #    ... (复杂操作，暂不需要，由 prune 逻辑处理)
            
        self._adjacency_dirty = True

    def prune_relation_hashes(self, operations: List[Tuple[str, str, str]]) -> None:
        """
        修剪特定关系哈希 (从 _edge_hash_map 移除; 如果边变空则从矩阵移除)
        
        Args:
           operations: List[(src, tgt, relation_hash)]
        """
        if not operations:
            return
            
        edges_to_check_removal = set()
        
        # 1. 更新映射 (Update Map)
        for src, tgt, h in operations:
             src_canon = self._canonicalize(src)
             tgt_canon = self._canonicalize(tgt)
             if src_canon in self._node_to_idx and tgt_canon in self._node_to_idx:
                 s_idx = self._node_to_idx[src_canon]
                 t_idx = self._node_to_idx[tgt_canon]
                 
                 key = (s_idx, t_idx)
                 if key in self._edge_hash_map:
                     if h in self._edge_hash_map[key]:
                         self._edge_hash_map[key].remove(h)
                         
                     if not self._edge_hash_map[key]:
                         del self._edge_hash_map[key]
                         edges_to_check_removal.add((src, tgt))
                         
        # 2. 从矩阵中移除空边 (Remove Empty Edges from Matrix)
        if edges_to_check_removal:
            self.deactivate_edges(list(edges_to_check_removal))
            self._total_edges_deleted += len(edges_to_check_removal)

    def get_low_weight_edges(self, threshold: float) -> List[Tuple[str, str]]:
        """
        获取低于阈值的边 (candidates for pruning/freezing)
        
        Args:
            threshold: 权重阈值
            
        Returns:
            List[(src, tgt)]: 边列表
        """
        if self._adjacency is None:
            return []
            
        # 获取所有非零元素
        rows, cols = self._adjacency.nonzero()
        data = self._adjacency.data
        
        low_weight_indices = np.where(data < threshold)[0]
        
        results = []
        for idx in low_weight_indices:
            r = rows[idx]
            c = cols[idx]
            src = self._nodes[r]
            tgt = self._nodes[c]
            results.append((src, tgt))
            
        return results

    def get_isolated_nodes(self, include_inactive: bool = True) -> List[str]:
        """
        获取孤儿节点 (Active Degree = 0)
        
        Args:
            include_inactive: 是否包含参与了inactive边(冻结边)的节点。
                              如果 True (默认推荐): 排除掉虽然active degree=0但存在于_edge_hash_map(冻结边)中的节点。
                              如果 False: 只要在 active matrix 里度为0就返回 (可能会误删冻结节点)。
                              
        Returns:
            孤儿节点名称列表
        """
        if self._adjacency is None:
            # 如果全空，则所有节点都是孤儿
            return self._nodes.copy()
            
        n = len(self._nodes)
        
        # 计算 Active Degree (In + Out)
        # 用 sum(axis) 会得到 dense matrix/array
        active_adj = self._adjacency
        out_degrees = np.array(active_adj.sum(axis=1)).flatten()
        in_degrees = np.array(active_adj.sum(axis=0)).flatten()
        
        # 处理悬挂节点 (dangling node check not really needed here, just sum)
        total_degrees = out_degrees + in_degrees
        
        # 找到 active degree = 0 的索引
        isolated_indices = np.where(total_degrees == 0)[0]
        
        if len(isolated_indices) == 0:
            return []
            
        isolated_nodes_set = {self._nodes[i] for i in isolated_indices}
        
        # 如果需要排除 Inactive 参与者
        if include_inactive and self._edge_hash_map:
            # 收集所有在冻结边中的 unique 节点索引
            frozen_participant_indices = set()
            for (u_idx, v_idx), hashes in self._edge_hash_map.items():
                if hashes: # 只要有 hash 存在 (哪怕 inactive)
                    frozen_participant_indices.add(u_idx)
                    frozen_participant_indices.add(v_idx)
            
            # 过滤
            final_isolated = []
            for idx in isolated_indices:
                if idx not in frozen_participant_indices:
                    final_isolated.append(self._nodes[idx])
            return final_isolated
            
        else:
            return list(isolated_nodes_set)

    def clear(self) -> None:
        """清空所有数据"""
        self._nodes.clear()
        self._node_to_idx.clear()
        self._node_attrs.clear()
        self._adjacency = None
        self._edge_hash_map.clear()
        self._adjacency_T = None
        self._adjacency_dirty = True
        self._total_nodes_added = 0
        self._total_edges_added = 0
        self._total_nodes_deleted = 0
        self._total_edges_deleted = 0
        logger.info("图存储已清空")

    def save(self, data_dir: Optional[Union[str, Path]] = None) -> None:
        """
        保存到磁盘

        Args:
            data_dir: 数据目录（默认使用初始化时的目录）
        """
        if data_dir is None:
            data_dir = self.data_dir

        if data_dir is None:
            raise ValueError("未指定数据目录")

        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        # 保存邻接矩阵
        if self._adjacency is not None:
            matrix_path = data_dir / "graph_adjacency.npz"
            with atomic_write(matrix_path, "wb") as f:
                save_npz(f, self._adjacency)
            logger.debug(f"保存邻接矩阵: {matrix_path}")

        # 保存元数据
        metadata = {
            "nodes": self._nodes,
            "node_to_idx": self._node_to_idx,
            "node_attrs": self._node_attrs,
            "matrix_format": self.matrix_format,
            "total_nodes_added": self._total_nodes_added,
            "total_edges_added": self._total_edges_added,
            "total_nodes_deleted": self._total_nodes_deleted,
            "total_edges_deleted": self._total_edges_deleted,
            "edge_hash_map": dict(self._edge_hash_map), # 持久化 V5 映射 (将 defaultdict 转换为普通 dict)
        }

        metadata_path = data_dir / "graph_metadata.pkl"
        with atomic_write(metadata_path, "wb") as f:
            pickle.dump(metadata, f)
        logger.debug(f"保存元数据: {metadata_path}")

        logger.info(f"图存储已保存到: {data_dir}")

    def load(self, data_dir: Optional[Union[str, Path]] = None) -> None:
        """
        从磁盘加载

        Args:
            data_dir: 数据目录（默认使用初始化时的目录）
        """
        if data_dir is None:
            data_dir = self.data_dir

        if data_dir is None:
            raise ValueError("未指定数据目录")

        data_dir = Path(data_dir)
        if not data_dir.exists():
            raise FileNotFoundError(f"数据目录不存在: {data_dir}")

        # 加载元数据
        metadata_path = data_dir / "graph_metadata.pkl"
        if not metadata_path.exists():
            raise FileNotFoundError(f"元数据文件不存在: {metadata_path}")

        with open(metadata_path, "rb") as f:
            metadata = pickle.load(f)

        # 恢复状态，并通过规范化处理旧数据中的重复项
        self._nodes = metadata["nodes"]
        self._node_attrs = {} # 重新构建以确保键名 (Key) 规范化
        self._node_to_idx = {} # 重新构建以确保键名 (Key) 规范化

        # 重新构建映射，处理旧数据中的碰撞
        for idx, node_name in enumerate(self._nodes):
            canon = self._canonicalize(node_name)
            if canon not in self._node_to_idx:
                self._node_to_idx[canon] = idx
            
            # 处理属性 (优先保留已有的)
            orig_attrs = metadata.get("node_attrs", {})
            if node_name in orig_attrs and canon not in self._node_attrs:
                self._node_attrs[canon] = orig_attrs[node_name]

        self.matrix_format = metadata["matrix_format"]
        self._total_nodes_added = metadata["total_nodes_added"]
        self._total_edges_added = metadata["total_edges_added"]
        self._total_nodes_deleted = metadata["total_nodes_deleted"]
        self._total_edges_deleted = metadata["total_edges_deleted"]
        
        # 恢复 V5 边哈希映射 (Restore V5 edge hash map)
        edge_map_data = metadata.get("edge_hash_map", {})
        # 重新初始化为 defaultdict(set)
        self._edge_hash_map = defaultdict(set)
        if edge_map_data:
            for k, v in edge_map_data.items():
                self._edge_hash_map[k] = set(v) # 确保类型为 set

        # 加载邻接矩阵
        matrix_path = data_dir / "graph_adjacency.npz"
        if matrix_path.exists():
            self._adjacency = load_npz(str(matrix_path))

            # 确保格式正确
            if self.matrix_format == "csc" and isinstance(self._adjacency, csr_matrix):
                self._adjacency = self._adjacency.tocsc()
            elif self.matrix_format == "csr" and isinstance(self._adjacency, csc_matrix):
                self._adjacency = self._adjacency.tocsr()

            logger.debug(f"加载邻接矩阵: {matrix_path}, shape={self._adjacency.shape}")

        # 检查维度不匹配并修复
        if self._adjacency is not None:
             adj_n = self._adjacency.shape[0]
             current_n = len(self._nodes)
             if current_n > adj_n:
                 logger.warning(f"检测到图存储维度不匹配: 节点数={current_n}, 矩阵大小={adj_n}. 正在自动修复...")
                 self._expand_adjacency_matrix(current_n - adj_n)

        self._adjacency_dirty = True
        logger.info(
            f"图存储已加载: {len(self._nodes)} 个节点, "
            f"{self._adjacency.nnz if self._adjacency is not None else 0} 条边"
        )

    def _expand_adjacency_matrix(self, added_nodes: int) -> None:
        """
        扩展邻接矩阵以容纳新节点
        
        Args:
            added_nodes: 新增节点数量
        """
        if self._adjacency is None:
            n = len(self._nodes)
            # 根据模式初始化

            if self._modification_mode == GraphModificationMode.INCREMENTAL:
                 self._adjacency = lil_matrix((n, n), dtype=np.float32)
            else:
                 self._adjacency = csr_matrix((n, n), dtype=np.float32)
            return

        old_n = self._adjacency.shape[0]
        new_n = old_n + added_nodes
        
        # 优化：根据模式选择不同的扩容策略
        if self._modification_mode == GraphModificationMode.INCREMENTAL:
            # LIL 格式可以直接 resize，非常高效
            try:
                if not isinstance(self._adjacency, lil_matrix):
                    self._adjacency = self._adjacency.tolil()
                
                self._adjacency.resize((new_n, new_n))
                # logger.debug(f"扩展 LIL 矩阵: {old_n} -> {new_n}")
            except Exception as e:
                logger.warning(f"LIL resize 失败，回退到通用方法: {e}")
                self._expand_generic(new_n, old_n)
                
        else:
            # CSR/CSC 格式使用 bmat 避免结构破坏警告
            try:
                # bmat 需要明确的形状，不能全部依赖 None
                added = new_n - old_n
                # 创建零矩阵块
                # 注意: 这里统一创建 CSR 零矩阵，bmat 会处理合并
                z_tr = csr_matrix((old_n, added), dtype=np.float32)
                z_bl = csr_matrix((added, old_n), dtype=np.float32)
                z_br = csr_matrix((added, added), dtype=np.float32)

                self._adjacency = bmat(
                    [[self._adjacency, z_tr], [z_bl, z_br]], 
                    format=self.matrix_format,
                    dtype=np.float32
                )
                # logger.debug(f"扩展矩阵 (bmat): {old_n} -> {new_n}")
            except Exception as e:
                logger.warning(f"bmat 扩展失败: {e}")
                self._expand_generic(new_n, old_n)

    def _expand_generic(self, new_n: int, old_n: int):
        """通用扩展方法（回退方案）"""
        if self.matrix_format == "csr":
            new_adjacency = csr_matrix((new_n, new_n), dtype=np.float32)
            new_adjacency[:old_n, :old_n] = self._adjacency
        else:
            new_adjacency = csc_matrix((new_n, new_n), dtype=np.float32)
            new_adjacency[:old_n, :old_n] = self._adjacency
        self._adjacency = new_adjacency
        self._adjacency_dirty = True
        
        # 如果都在增量模式，确保是LIL
        if self._modification_mode == GraphModificationMode.INCREMENTAL:
             try:
                 self._adjacency = self._adjacency.tolil()
             except:
                 pass

    @property
    def num_nodes(self) -> int:
        """节点数量"""
        return len(self._nodes)

    @property
    def num_edges(self) -> int:
        """边数量"""
        if self._adjacency is None:
            return 0
        return int(self._adjacency.nnz)

    @property
    def density(self) -> float:
        """
        图密度（实际边数 / 可能的最大边数）

        有向图: E / (V * (V - 1))
        无向图: 2E / (V * (V - 1))

        这里按有向图计算
        """
        if self.num_nodes < 2:
            return 0.0

        max_edges = self.num_nodes * (self.num_nodes - 1)
        return self.num_edges / max_edges if max_edges > 0 else 0.0

    def __len__(self) -> int:
        """节点数量"""
        return self.num_nodes

    def has_data(self) -> bool:
        """检查磁盘上是否存在现有数据"""
        if self.data_dir is None:
            return False
        return (self.data_dir / "graph_metadata.pkl").exists()

    def __repr__(self) -> str:
        return (
            f"GraphStore(nodes={self.num_nodes}, edges={self.num_edges}, "
            f"density={self.density:.4f}, format={self.matrix_format})"
        )

    def rebuild_edge_hash_map(self, triples: List[Tuple[str, str, str, str]]) -> int:
        """
        从元数据重建 V5 边哈希映射 (Migration Tool)
        
        Args:
            triples: List of (s, p, o, hash)
            
        Returns:
            count of mapped hashes
        """
        count = 0
        self._edge_hash_map = defaultdict(set)
        
        for s, p, o, h in triples:
            if not h: continue
            
            s_canon = self._canonicalize(s)
            o_canon = self._canonicalize(o)
            
            if s_canon in self._node_to_idx and o_canon in self._node_to_idx:
                u = self._node_to_idx[s_canon]
                v = self._node_to_idx[o_canon]
                
                # 如果是双向的，通常在元数据中存储为有向，而 GraphStore 也通常是有向的。
                # 映射键对应特定的边方向。
                self._edge_hash_map[(u, v)].add(h)
                count += 1
                
        self._adjacency_dirty = True
        logger.info(f"已从 {count} 条哈希重建边哈希映射，覆盖 {len(self._edge_hash_map)} 条边")
        return count


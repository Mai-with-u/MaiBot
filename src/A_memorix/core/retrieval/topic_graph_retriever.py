"""基于无监督软话题重要性求解、FAISS 双池粗召回与图连通扩散的零 LLM 记忆检索器。

核心机制：
1. 离线/批处理索引阶段（TopicImportanceSolver）：
   - 仅保留紧凑拓扑与轻量随机探针草图（64 维行采样），不在内存中常驻全库段落/实体/关系稠密向量矩阵。
   - 融合字面共现校验的结构边与去锥偏移同义补边，构建度归一化传播算子。
   - 在去均值语义残差子空间上执行软分配球面 EM 聚类（Soft Spherical K-Means），支持单段记忆跨多话题软归属。
   - 批量求解全局 PageRank 与各话题个性化 PageRank，剥离全局高频 Hub 中心性并融入时间新鲜度因子，
     得到节点话题重要性矩阵 NodeImp(u | t) 与段落话题重要性矩阵 I(m | t)。
   - 支持小批量新增记忆的在线增量投影（O(ΔN)），无需每次写入推倒重算 K-Means 与全图 PPR。
2. 在线近期聊天检索阶段（TopicGraphRetriever）：
   - 接收最新若干条聊天消息（零 LLM 调用），通过 FAISS 双池（VectorStore.search）执行候选粗召回。
   - 基于轻量探针草图与极值统计（Extreme Value Theory）实时估计每条消息的零分布（μ_0, σ_0），
     独立计算各条消息的记忆锚点显著性（位置无关自举赋权，不依赖“最后一条必为正题”假设）。
   - 激活话题门控下沿稀疏图做 1~2 跳局部连通扩散，具备对纯日常闲聊的零召回拒识能力。
"""

from contextlib import nullcontext
from dataclasses import dataclass, field
from math import log, sqrt
from threading import RLock
from typing import Any, Collection, ContextManager, Dict, List, Optional, Sequence, Set, Tuple

import re
import time

from scipy.sparse import coo_matrix, csr_matrix, diags
import faiss
import numpy as np

from src.common.logger import get_logger

from ..embedding import EmbeddingAPIAdapter
from ..storage import GraphStore, MetadataStore, VectorStore
from ..utils.async_work import cancellation_safe_to_thread
from ..utils.hash import compute_hash
from ..utils.matcher import AhoCorasick
from ..utils.metadata import coerce_metadata_dict
from .dual_path import RetrievalResult, RetrievalScope
from .sparse_bm25 import SparseBM25Index

logger = get_logger("A_Memorix.TopicGraphRetriever")

_PURE_NOISE_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
_REPEATED_LAUGH_RE = re.compile(r"^[哈嘿嘻呵啊哦嗯唔啦呀噢唉卧槽草6\s\W_]+$", re.UNICODE)
_SLOT_CONFLICT_TOKEN_RE = re.compile(
    r"(?:周[一二三四五六日天末]|星期[一二三四五六日天]|礼拜[一二三四五六日天]"
    r"|\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万亿]+)",
    re.UNICODE,
)

# 过滤日常对话泛化高频词，防止 OpenIE 抽出的泛化双字节点污染字面精确唤醒
_GENERIC_STOP_ENTITIES: Set[str] = {
    "今天",
    "明天",
    "昨天",
    "后天",
    "前天",
    "今晚",
    "昨晚",
    "上午",
    "下午",
    "中午",
    "晚上",
    "早上",
    "最近",
    "刚才",
    "现在",
    "之前",
    "以后",
    "时候",
    "时间",
    "大家",
    "我们",
    "你们",
    "他们",
    "她们",
    "自己",
    "别人",
    "有人",
    "群友",
    "群里",
    "群主",
    "东西",
    "事情",
    "问题",
    "情况",
    "感觉",
    "意思",
    "地方",
    "方面",
    "结果",
    "原因",
    "办法",
    "消息",
    "内容",
    "聊天",
    "说话",
    "哈哈",
    "什么",
    "怎么",
    "哪里",
    "哪个",
    "多少",
    "为什么",
    "这个",
    "那个",
    "这些",
    "那些",
    "这里",
    "那里",
}


@dataclass(frozen=True)
class GraphSnapshot:
    """主事件循环锁内捕获的一致性图结构快照。"""

    node_names: List[str]
    raw_adj: csr_matrix
    graph_revision: int
    node_revision: int


@dataclass
class TopicGraphConfig:
    """话题重要性求解与图连通检索配置。"""

    min_topics: int = 2
    max_topics: int = 32
    topic_count_scale: float = 1.5
    kmeans_iterations: int = 16
    soft_topic_temperature: float = 0.14
    graph_smoothing_beta: float = 0.35
    node_smoothing_lambda: float = 0.35
    probe_sketch_size: int = 64
    incremental_drift_ratio: float = 0.22

    # 同义语义补边配置
    enable_synonym_edges: bool = True
    synonym_similarity_threshold: float = 0.72
    synonym_top_k: int = 6
    synonym_edge_weight: float = 0.60

    # 话题个性化 PageRank、去偏与时效配置
    ppr_damping: float = 0.80
    ppr_max_iterations: int = 40
    ppr_tolerance: float = 1e-6
    hub_debias_gamma: float = 0.65
    paragraph_graph_importance_ratio: float = 0.58
    temporal_freshness_strength: float = 0.14

    # 在线 FAISS 双池候选预算与近期聊天检索配置
    query_instruction_prefix: str = ""
    csls_debias_weight: float = 0.50
    max_recent_messages: int = 6
    recency_tiebreak_strength: float = 0.20
    recency_decay: float = 0.72
    candidate_paragraph_top_k: int = 32
    candidate_graph_top_k: int = 96
    active_top_topics: int = 2
    topic_routing_temperature: float = 0.10
    hop1_decay: float = 0.65
    hop2_decay: float = 0.34
    topic_gate_strength: float = 0.72

    # 极值分布显著性与闲聊拒识门控配置（区分整窗触发门槛与多跳支撑入选下限）
    evt_false_alarm_alpha: float = 0.032
    evt_noise_scale: float = 1.00
    query_trigger_threshold: float = 0.25
    min_relevance_score: float = 0.12

    # 最终多通道融合配置
    semantic_weight: float = 0.32
    graph_connectivity_weight: float = 0.48
    sparse_weight: float = 0.20
    topic_modulation_strength: float = 0.24
    topic_prior_boost_weight: float = 0.12

    def __post_init__(self) -> None:
        if self.min_topics < 1:
            raise ValueError(f"min_topics 必须 >= 1: {self.min_topics}")
        if self.max_topics < self.min_topics:
            raise ValueError(f"max_topics ({self.max_topics}) 不能小于 min_topics ({self.min_topics})")
        if not 0.0 < self.ppr_damping < 1.0:
            raise ValueError(f"ppr_damping 必须在 (0, 1) 之间: {self.ppr_damping}")
        if not 0.0 <= self.hub_debias_gamma <= 2.0:
            raise ValueError(f"hub_debias_gamma 必须在 [0, 2] 之间: {self.hub_debias_gamma}")


@dataclass(frozen=True)
class TopicClusterSummary:
    """单个软话题簇的可解释摘要。"""

    topic_id: int
    effective_paragraph_mass: float
    top_entities: Tuple[str, ...]
    top_paragraph_hashes: Tuple[str, ...]


@dataclass
class TopicImportanceIndex:
    """预求解完成的话题-图节点-记忆重要性紧凑索引（不常驻全库稠密向量或全文）。"""

    cache_key: Tuple[Any, ...]
    built_at: float
    dimension: int
    full_build_count: int
    incremental_update_count: int
    base_paragraph_count: int

    # 段落紧凑视图（正文与元数据按最终 Top-K 延迟回表读取）
    paragraph_hashes: List[str]
    paragraph_hash_to_idx: Dict[str, int]
    paragraph_timestamps: np.ndarray  # (N_P,), float64
    paragraph_freshness: np.ndarray  # (N_P,), float32 in [1 - strength, 1.0]
    paragraph_csls_bias: np.ndarray  # (N_P,), float32 跨粒度长度/Hubness 余弦偏置

    # 图节点紧凑视图
    node_names: List[str]
    node_hashes: List[str]
    node_name_to_idx: Dict[str, int]
    node_hash_to_idx: Dict[str, int]
    node_df: np.ndarray  # (N_V,), float32
    node_idf: np.ndarray  # (N_V,), float32
    node_csls_bias: np.ndarray  # (N_V,), float32
    literal_matcher: AhoCorasick
    pattern_to_node_indices: Dict[str, Tuple[int, ...]]

    # 关系紧凑视图
    relation_hashes: List[str]
    rel_hash_to_idx: Dict[str, int]
    relation_endpoints: np.ndarray  # (N_R, 2), int32 (-1 if missing node)
    relation_weights: np.ndarray  # (N_R,), float32
    relation_csls_bias: np.ndarray  # (N_R,), float32

    # 稀疏图算子与关联矩阵
    norm_propagation: csr_matrix  # (N_V, N_V), D^{-1/2} A D^{-1/2}
    paragraph_node_matrix: csr_matrix  # (N_P, N_V)
    paragraph_relation_matrix: csr_matrix  # (N_P, N_R)
    paragraph_relation_lists: List[List[Tuple[int, float]]]

    # 轻量级分粒度同质探针草图（段落/实体/关系独立零分布，消除名词-陈述双峰混合偏移）
    cone_baseline: float
    para_tail_factor: float
    entity_tail_factor: float
    relation_tail_factor: float
    query_cone_shift: np.ndarray  # (D,), float32 查询侧各向异性偏移向量
    para_probe_vectors: np.ndarray  # (M_P <= 48, D), float32
    para_probe_csls_bias: np.ndarray  # (M_P <= 48,), float32
    entity_probe_vectors: np.ndarray  # (M_E <= 48, D), float32
    entity_probe_csls_bias: np.ndarray  # (M_E <= 48,), float32
    relation_probe_vectors: np.ndarray  # (M_R <= 48, D), float32
    relation_probe_csls_bias: np.ndarray  # (M_R <= 48,), float32
    fallback_para_null: Tuple[float, float]  # (mean, std)
    fallback_entity_null: Tuple[float, float]  # (mean, std)
    fallback_relation_null: Tuple[float, float]  # (mean, std)

    # 话题求解产物
    topic_count: int
    topic_smoothed_centroids: np.ndarray  # (K, D), float32
    topic_query_centroids: np.ndarray  # (K, D), float32
    global_node_pagerank: np.ndarray  # (N_V,), float32
    node_topic_importance: np.ndarray  # (N_V, K), float32 in [0, 1]
    paragraph_topic_importance: np.ndarray  # (N_P, K), float32 in [0, 1]
    topic_summaries: List[TopicClusterSummary] = field(default_factory=list)


def _l2_normalize_rows(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """按行做 L2 归一化，返回归一化矩阵与非零行掩码。"""
    if matrix.size == 0:
        return matrix.astype(np.float32, copy=False), np.zeros((matrix.shape[0],), dtype=bool)
    out = np.array(matrix, dtype=np.float32, order="C", copy=True)
    norms = np.linalg.norm(out, axis=1)
    valid = norms > 1e-8
    if np.any(valid):
        out[valid] /= norms[valid, None]
    if np.any(~valid):
        out[~valid] = 0.0
    return out, valid


def _deterministic_sample_indices(total: int, max_count: int) -> np.ndarray:
    """确定性均匀步长采样索引，避免随机种子漂移。"""
    if total <= 0 or max_count <= 0:
        return np.zeros((0,), dtype=np.int32)
    if total <= max_count:
        return np.arange(total, dtype=np.int32)
    return np.linspace(0, total - 1, num=max_count, dtype=np.int32)


def _estimate_query_null_distribution(
    query_vecs: np.ndarray,
    probe_vecs: np.ndarray,
    fallback: Tuple[float, float],
    probe_csls_bias: Optional[np.ndarray] = None,
    csls_weight: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """用分粒度同质探针草图估计每条消息与无关语料的无偏背景余弦均值 μ_0 与标准差 σ_0。

    当启用 CSLS 去偏时同步扣除探针项偏置，确保背景零分布与候选打分处于完全一致的度量空间；
    仅使用探针分布的下 58% 截尾主体估计标准差，并对 38% 分位值做标准正态截断中心偏差补偿 (+0.28σ)，
    消除分位估计偏低导致的假阳性膨胀。
    """
    num_q = int(query_vecs.shape[0])
    if probe_vecs.shape[0] < 4:
        mu = np.full((num_q,), float(fallback[0]), dtype=np.float32)
        sigma = np.full((num_q,), max(0.03, float(fallback[1])), dtype=np.float32)
        return mu, sigma

    sims = query_vecs @ probe_vecs.T  # (M, M_probe)
    if probe_csls_bias is not None and probe_csls_bias.shape[0] == probe_vecs.shape[0] and csls_weight > 0.0:
        sims = sims - float(csls_weight) * probe_csls_bias[None, :]
    q38_arr = np.quantile(sims, 0.38, axis=1)
    upper_cut = np.quantile(sims, 0.58, axis=1, keepdims=True)
    mu_arr = np.zeros((num_q,), dtype=np.float32)
    sigma_arr = np.zeros((num_q,), dtype=np.float32)

    for i in range(num_q):
        q38_val = float(q38_arr[i])
        bulk = sims[i, sims[i] <= float(upper_cut[i, 0]) + 1e-7]
        if bulk.size >= 3:
            mad = float(np.median(np.abs(bulk - float(np.median(bulk)))))
            raw_bulk_std = max(1.4826 * mad, float(np.std(bulk))) / 0.675
        else:
            raw_bulk_std = float(fallback[1])

        headroom = max(0.15, 1.0 - max(0.0, q38_val))
        pop_std = float(fallback[1])
        if raw_bulk_std <= 1e-3:
            # 仅对零方差正交合成测试向量启用合成安全下界，不扭曲真实稀疏/稠密嵌入的经验标准差
            sigma_i = 0.055 * headroom
            mu_i = q38_val
        else:
            # 经验贝叶斯方差收缩：将单句小样本截尾标准差向全库 2000+ 无关对总体标准差收缩，消除 Student-t 分母塌缩假阳性
            if pop_std > 1e-3:
                eb_std = sqrt(0.50 * (raw_bulk_std * raw_bulk_std) + 0.50 * (pop_std * pop_std))
                sigma_i = max(0.012 * headroom, 0.90 * pop_std, eb_std)
            else:
                sigma_i = max(0.012 * headroom, raw_bulk_std)
            mu_i = q38_val + 0.305 * sigma_i

        mu_arr[i] = mu_i
        sigma_arr[i] = sigma_i

    return mu_arr, sigma_arr


def _evt_noise_ceiling(
    pool_size: int,
    scale: float,
    *,
    num_queries: int = 1,
    alpha: float = 0.032,
    probe_count: int = 48,
) -> float:
    """按 Šidák 极值分位结合 Cornish-Fisher 有限探针自由度校正求解精确噪声上界。"""
    eff_pool = float(max(4, int(pool_size))) ** 0.82
    eff_trials = max(4.0, float(max(1, int(num_queries))) * eff_pool)
    tail_p = min(0.20, max(1e-8, float(alpha) / eff_trials))
    two_neg_ln_p = -2.0 * log(tail_p)
    z_alpha = sqrt(max(1.80, two_neg_ln_p - log(max(1.0, 3.14159265 * two_neg_ln_p))))
    # 有限探针估计 (μ_0, σ_0) 引入 Student-t 重尾，按有效自由度 ν 做一阶 Cornish-Fisher 膨胀校正
    nu_eff = max(14.0, 0.75 * float(max(8, int(probe_count))))
    t_alpha = z_alpha + (z_alpha * z_alpha * z_alpha + z_alpha) / (4.0 * nu_eff)
    return float(scale) * t_alpha


def _compute_temporal_freshness(timestamps: np.ndarray, strength: float) -> np.ndarray:
    """将段落时间戳映射为平滑时效因子 [1 - strength, 1.0]，使新近状态修订自然优先于陈旧记录。"""
    n = int(timestamps.shape[0])
    if n == 0 or strength <= 0.0:
        return np.ones((n,), dtype=np.float32)
    valid = timestamps > 0.0
    if int(np.sum(valid)) < 2:
        return np.ones((n,), dtype=np.float32)
    t_vals = timestamps[valid]
    t_min = float(np.min(t_vals))
    t_max = float(np.max(t_vals))
    span_sec = t_max - t_min
    if span_sec <= 5.0:
        return np.ones((n,), dtype=np.float32)

    # 同时结合时间跨度归一化与分位秩；跨度不足 10 分钟时平滑衰减强度，消除批量导入毫秒级先后偏置
    span_maturity = min(1.0, span_sec / 600.0)
    span_norm = np.clip((timestamps - t_min) / span_sec, 0.0, 1.0)
    order = np.argsort(timestamps)
    ranks = np.empty((n,), dtype=np.float32)
    ranks[order] = np.linspace(0.0, 1.0, num=n, dtype=np.float32)
    blended = 0.5 * span_norm.astype(np.float32) + 0.5 * ranks
    eff_s = float(np.clip(strength, 0.0, 0.40)) * span_maturity
    out = np.where(valid, (1.0 - eff_s) + eff_s * blended, 1.0 - 0.5 * eff_s)
    return out.astype(np.float32, copy=False)


def _is_specific_literal_pattern(pattern: str, df: float, num_paragraphs: int) -> bool:
    """判定实体词或别名是否具备足够的字面唤醒特异性（过滤泛化高频词与过短噪声）。"""
    clean = pattern.strip().lower()
    if len(clean) < 2 or clean in _GENERIC_STOP_ENTITIES:
        return False
    if _PURE_NOISE_RE.match(clean) or _REPEATED_LAUGH_RE.match(clean):
        return False
    # 双字词若在超过 35% 的段落中泛化出现（且段落数 >= 8），不再作为无条件字面强锚点
    if len(clean) == 2 and num_paragraphs >= 8 and df > max(3.0, 0.35 * float(num_paragraphs)):
        return False
    return True


def _has_conflicting_slot_tokens(name_u: str, name_v: str) -> bool:
    """检测两个实体名是否包含互斥的数值、日期或星期槽位（如“周一”与“周五”、“800”与“1000”），防止连出伪同义边。"""
    toks_u = set(_SLOT_CONFLICT_TOKEN_RE.findall(name_u.lower()))
    if not toks_u:
        return False
    toks_v = set(_SLOT_CONFLICT_TOKEN_RE.findall(name_v.lower()))
    if not toks_v:
        return False
    return toks_u != toks_v


def _compute_csls_item_bias(
    item_vectors: np.ndarray,
    item_valid: np.ndarray,
    ref_probes: np.ndarray,
) -> np.ndarray:
    """计算一维 CSLS 局部背景余弦偏置（仅占 4B/项）：同时抑制向量空间全能 Hub 并补偿长对话块的余弦稀释。"""
    n_items = int(item_vectors.shape[0])
    if n_items == 0 or ref_probes.shape[0] < 4 or int(np.sum(item_valid)) < 3:
        return np.zeros((n_items,), dtype=np.float32)

    sims = item_vectors @ ref_probes.T  # (N, M_probe)
    item_bg = np.quantile(sims, 0.45, axis=1)
    corpus_center = float(np.median(item_bg[item_valid]))
    bias = np.where(item_valid, np.clip(item_bg - corpus_center, -0.10, 0.14), 0.0)
    return bias.astype(np.float32, copy=False)


def _estimate_tail_skewness_factor(sim_values: np.ndarray) -> Tuple[float, float, float]:
    """从已剔除结构共现对的无关探针交叉余弦样本估计无偏 (μ, σ) 及右尾重度校准系数 λ_tail ∈ [0.94, 1.22]。"""
    flat = sim_values.ravel()
    if flat.size < 8:
        mu = float(np.median(flat)) if flat.size > 0 else 0.0
        std = max(0.055, float(np.std(flat))) if flat.size > 1 else 0.08
        return mu, std, 1.0

    q38 = float(np.quantile(flat, 0.38))
    upper = float(np.quantile(flat, 0.58))
    bulk = flat[flat <= upper + 1e-7]
    if bulk.size >= 4:
        mad = float(np.median(np.abs(bulk - float(np.median(bulk)))))
        raw_bulk_std = max(1.4826 * mad, float(np.std(bulk))) / 0.675
    else:
        raw_bulk_std = float(np.std(flat))

    headroom = max(0.15, 1.0 - max(0.0, q38))
    if raw_bulk_std <= 1e-3:
        sigma = 0.055 * headroom
        mu = q38
    else:
        sigma = max(0.012 * headroom, raw_bulk_std)
        mu = q38 + 0.28 * raw_bulk_std

    if flat.size >= 16 and raw_bulk_std > 1e-3:
        # 标准正态下 (q_90 - q_38) / σ = 1.2816 - (-0.3055) = 1.5871；以此度量真实无关对的右尾重度
        q90 = float(np.quantile(flat, 0.90))
        emp_span90 = (q90 - q38) / sigma
        tail_factor = float(np.clip(emp_span90 / 1.5871, 0.94, 1.22))
    else:
        tail_factor = 1.0

    return mu, sigma, tail_factor


class TopicImportanceSolver:
    """无监督软话题发现、去偏重要性求解与在线增量投影器。"""

    def __init__(self, config: Optional[TopicGraphConfig] = None):
        self.config = config or TopicGraphConfig()

    @staticmethod
    def _load_alias_map(metadata_store: MetadataStore, node_name_to_idx: Dict[str, int]) -> Dict[str, List[int]]:
        """从人物身份别名表加载群昵称/简称到规范图节点的映射。"""
        alias_map: Dict[str, List[int]] = {}
        try:
            rows = metadata_store.query(
                """
                SELECT LOWER(TRIM(a.alias)) AS alias_low, LOWER(TRIM(p.canonical_name)) AS canon_low
                FROM person_identity_aliases a
                JOIN person_identities p ON p.person_id = a.person_id
                WHERE a.alias IS NOT NULL AND TRIM(a.alias) != ''
                """
            )
        except Exception:
            return alias_map

        for row in rows:
            alias_low = str(row.get("alias_low") or "").strip()
            canon_low = str(row.get("canon_low") or "").strip()
            u_idx = node_name_to_idx.get(canon_low)
            if not alias_low or u_idx is None or len(alias_low) < 2 or alias_low in _GENERIC_STOP_ENTITIES:
                continue
            bucket = alias_map.setdefault(alias_low, [])
            if u_idx not in bucket:
                bucket.append(u_idx)
        return alias_map

    def _build_literal_matcher(
        self,
        *,
        node_names: List[str],
        node_df: np.ndarray,
        num_paragraphs: int,
        metadata_store: MetadataStore,
        node_name_to_idx: Dict[str, int],
    ) -> Tuple[AhoCorasick, Dict[str, Tuple[int, ...]]]:
        """构建带泛化词过滤与人物别名映射的 Aho-Corasick 自动机。"""
        pattern_buckets: Dict[str, List[int]] = {}
        for u_idx, name in enumerate(node_names):
            low = name.strip().lower()
            if _is_specific_literal_pattern(low, float(node_df[u_idx]), num_paragraphs):
                pattern_buckets.setdefault(low, []).append(u_idx)

        alias_map = self._load_alias_map(metadata_store, node_name_to_idx)
        for alias_low, u_indices in alias_map.items():
            if not _is_specific_literal_pattern(alias_low, 1.0, num_paragraphs):
                continue
            bucket = pattern_buckets.setdefault(alias_low, [])
            for u_idx in u_indices:
                if u_idx not in bucket:
                    bucket.append(u_idx)

        matcher = AhoCorasick()
        frozen_map: Dict[str, Tuple[int, ...]] = {}
        for pat, indices in pattern_buckets.items():
            matcher.add_pattern(pat)
            frozen_map[pat] = tuple(indices)
        matcher.build()
        return matcher, frozen_map

    @staticmethod
    def _fetch_vectors_matrix(
        store: VectorStore,
        ids: Sequence[str],
        dimension: int,
        *,
        typed_prefix: str = "",
    ) -> Tuple[np.ndarray, np.ndarray]:
        """按分批流式接口读取构建期临时归一化向量矩阵（构建完成后即释放，不驻留索引）。"""
        count = len(ids)
        mat = np.zeros((count, dimension), dtype=np.float32)
        if count == 0:
            return mat, np.zeros((0,), dtype=bool)

        id_to_pos = {item: idx for idx, item in enumerate(ids)}
        if typed_prefix:
            typed_ids = [f"{typed_prefix}{item}" for item in ids]
            typed_to_pos = {f"{typed_prefix}{item}": idx for idx, item in enumerate(ids)}
            found_pos: Set[int] = set()
            for batch in store.iter_vectors_by_ids(typed_ids, batch_size=1024):
                for key, vec in batch.items():
                    pos = typed_to_pos.get(key)
                    if pos is not None and vec.shape[0] == dimension:
                        mat[pos] = vec
                        found_pos.add(pos)
            missing_raw = [ids[i] for i in range(count) if i not in found_pos]
            if missing_raw:
                for batch in store.iter_vectors_by_ids(missing_raw, batch_size=1024):
                    for key, vec in batch.items():
                        pos = id_to_pos.get(key)
                        if pos is not None and vec.shape[0] == dimension:
                            mat[pos] = vec
        else:
            for batch in store.iter_vectors_by_ids(ids, batch_size=1024):
                for key, vec in batch.items():
                    pos = id_to_pos.get(key)
                    if pos is not None and vec.shape[0] == dimension:
                        mat[pos] = vec
        return _l2_normalize_rows(mat)

    def build_index(
        self,
        *,
        paragraph_vector_store: VectorStore,
        graph_vector_store: VectorStore,
        graph_snapshot: GraphSnapshot,
        metadata_store: MetadataStore,
        cache_key: Tuple[Any, ...],
        previous_full_builds: int = 0,
    ) -> TopicImportanceIndex:
        """从存储层构建紧凑话题重要性索引（构建期临时向量矩阵在函数返回时即释放）。"""
        started = time.perf_counter()
        dimension = int(paragraph_vector_store.dimension)

        # 1. 读取段落标识与时间戳（不把全文和元数据字典常驻内存）
        para_rows = metadata_store.query(
            """
            SELECT hash, LOWER(content) AS content_low,
                   COALESCE(event_time_end, event_time, updated_at, created_at, 0.0) AS eff_time
            FROM paragraphs
            WHERE (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY hash ASC
            """
        )
        paragraph_hashes: List[str] = []
        paragraph_contents_lower: List[str] = []
        ts_list: List[float] = []
        for row in para_rows:
            p_hash = str(row["hash"]).strip()
            if not p_hash:
                continue
            paragraph_hashes.append(p_hash)
            paragraph_contents_lower.append(str(row["content_low"] or ""))
            ts_list.append(float(row["eff_time"] or 0.0))

        num_paragraphs = len(paragraph_hashes)
        paragraph_hash_to_idx = {h: idx for idx, h in enumerate(paragraph_hashes)}
        paragraph_timestamps = np.asarray(ts_list, dtype=np.float64)
        paragraph_freshness = _compute_temporal_freshness(
            paragraph_timestamps,
            float(self.config.temporal_freshness_strength),
        )

        paragraph_vectors, paragraph_has_vector = self._fetch_vectors_matrix(
            paragraph_vector_store,
            paragraph_hashes,
            dimension,
        )

        # 2. 对齐一致性图快照与节点向量
        node_names = list(graph_snapshot.node_names)
        raw_adj = graph_snapshot.raw_adj
        num_nodes = len(node_names)
        node_name_to_idx = {name: idx for idx, name in enumerate(node_names)}
        node_hashes = [compute_hash(name) for name in node_names]
        node_hash_to_idx = {h: idx for idx, h in enumerate(node_hashes)}

        node_vectors, node_has_vector = self._fetch_vectors_matrix(
            graph_vector_store,
            node_hashes,
            dimension,
            typed_prefix="entity:",
        )

        # 3. 构建段落-节点关联矩阵与节点 IDF
        pe_rows = (
            metadata_store.query(
                """
                SELECT pe.paragraph_hash, pe.entity_hash, COALESCE(pe.mention_count, 1) AS mention_count
                FROM paragraph_entities pe
                JOIN paragraphs p ON p.hash = pe.paragraph_hash
                WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
                """
            )
            if num_paragraphs > 0 and num_nodes > 0
            else []
        )

        node_df = np.zeros((num_nodes,), dtype=np.float32)
        pe_p_indices: List[int] = []
        pe_u_indices: List[int] = []
        pe_weights: List[float] = []
        node_grounded_paragraphs: List[Set[int]] = [set() for _ in range(num_nodes)]

        for row in pe_rows:
            p_idx = paragraph_hash_to_idx.get(str(row["paragraph_hash"]))
            u_idx = node_hash_to_idx.get(str(row["entity_hash"]))
            if p_idx is None or u_idx is None:
                continue
            mention_count = max(1, int(row["mention_count"]))
            node_name = node_names[u_idx]
            is_grounded = bool(node_name and node_name.lower() in paragraph_contents_lower[p_idx])
            grounding_factor = 1.0 if is_grounded else 0.55
            if is_grounded:
                node_grounded_paragraphs[u_idx].add(p_idx)
            node_df[u_idx] += 1.0
            pe_p_indices.append(p_idx)
            pe_u_indices.append(u_idx)
            pe_weights.append(log(1.0 + float(mention_count)) * grounding_factor)

        node_idf = (np.log((float(num_paragraphs) + 1.0) / (node_df + 1.0)) + 1.0).astype(np.float32, copy=False)

        literal_matcher, pattern_to_node_indices = self._build_literal_matcher(
            node_names=node_names,
            node_df=node_df,
            num_paragraphs=num_paragraphs,
            metadata_store=metadata_store,
            node_name_to_idx=node_name_to_idx,
        )

        if pe_weights:
            weighted_vals = np.asarray(pe_weights, dtype=np.float32) * node_idf[np.asarray(pe_u_indices, dtype=np.int32)]
            raw_pn = coo_matrix(
                (weighted_vals, (pe_p_indices, pe_u_indices)),
                shape=(num_paragraphs, num_nodes),
                dtype=np.float32,
            ).tocsr()
            row_sums = np.asarray(raw_pn.sum(axis=1)).ravel()
            inv_row = np.zeros_like(row_sums, dtype=np.float32)
            nz_rows = row_sums > 1e-8
            inv_row[nz_rows] = 1.0 / np.sqrt(row_sums[nz_rows])
            paragraph_node_matrix = (diags(inv_row) @ raw_pn).tocsr()
        else:
            raw_pn = csr_matrix((num_paragraphs, num_nodes), dtype=np.float32)
            paragraph_node_matrix = raw_pn

        # 4. 读取活跃关系与支撑映射（融合置信度与记忆保持强度 retention_strength）
        rel_rows = metadata_store.query(
            """
            SELECT hash, subject, predicate, object,
                   COALESCE(confidence, 1.0) AS confidence,
                   COALESCE(retention_strength, 1.0) AS retention_strength
            FROM relations
            WHERE (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY hash ASC
            """
        )
        relation_hashes: List[str] = []
        relation_triples: List[Tuple[str, str, str]] = []
        relation_endpoints_list: List[Tuple[int, int]] = []
        relation_weight_list: List[float] = []
        rel_hash_to_idx: Dict[str, int] = {}

        for row in rel_rows:
            r_hash = str(row["hash"]).strip()
            if not r_hash:
                continue
            subj = str(row["subject"] or "").strip().lower()
            pred = str(row["predicate"] or "").strip().lower()
            obj = str(row["object"] or "").strip().lower()
            conf = max(0.05, float(row["confidence"]))
            ret_s = max(0.20, min(1.25, float(row["retention_strength"])))
            u_idx = node_name_to_idx.get(subj, -1)
            v_idx = node_name_to_idx.get(obj, -1)
            rel_hash_to_idx[r_hash] = len(relation_hashes)
            relation_hashes.append(r_hash)
            relation_triples.append((subj, pred, obj))
            relation_endpoints_list.append((u_idx, v_idx))
            relation_weight_list.append(conf * (0.75 + 0.25 * ret_s))

        num_relations = len(relation_hashes)
        relation_endpoints = (
            np.asarray(relation_endpoints_list, dtype=np.int32)
            if num_relations > 0
            else np.zeros((0, 2), dtype=np.int32)
        )
        relation_weights = (
            np.asarray(relation_weight_list, dtype=np.float32) if num_relations > 0 else np.zeros((0,), dtype=np.float32)
        )

        relation_vectors, relation_has_vector = self._fetch_vectors_matrix(
            graph_vector_store,
            relation_hashes,
            dimension,
            typed_prefix="relation:",
        )

        pr_rows = (
            metadata_store.query(
                """
                SELECT pr.paragraph_hash, pr.relation_hash
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
                """
            )
            if num_paragraphs > 0 and num_relations > 0
            else []
        )
        pr_p_indices: List[int] = []
        pr_r_indices: List[int] = []
        pr_weights: List[float] = []
        paragraph_relation_lists: List[List[Tuple[int, float]]] = [[] for _ in range(num_paragraphs)]

        for row in pr_rows:
            p_idx = paragraph_hash_to_idx.get(str(row["paragraph_hash"]))
            r_idx = rel_hash_to_idx.get(str(row["relation_hash"]))
            if p_idx is None or r_idx is None:
                continue
            subj, pred, obj = relation_triples[r_idx]
            text_low = paragraph_contents_lower[p_idx]
            subj_in = bool(subj and subj in text_low)
            obj_in = bool(obj and obj in text_low)
            pred_in = bool(pred and pred in text_low)
            if subj_in and obj_in:
                grounding = 1.0
            elif subj_in or obj_in:
                grounding = 0.62 if pred_in else 0.50
            else:
                grounding = 0.25
            weight = grounding * float(relation_weights[r_idx])
            pr_p_indices.append(p_idx)
            pr_r_indices.append(r_idx)
            pr_weights.append(weight)
            paragraph_relation_lists[p_idx].append((r_idx, weight))

        if pr_weights:
            paragraph_relation_matrix = coo_matrix(
                (np.asarray(pr_weights, dtype=np.float32), (pr_p_indices, pr_r_indices)),
                shape=(num_paragraphs, num_relations),
                dtype=np.float32,
            ).tocsr()
        else:
            paragraph_relation_matrix = csr_matrix((num_paragraphs, num_relations), dtype=np.float32)

        # 释放临时全文小写副本
        del paragraph_contents_lower

        # 5. 构建段落/实体/关系三池同质探针草图、一维 CSLS 偏置与剔除共现污染的后备零分布参数
        sketch_k = min(48, max(16, int(self.config.probe_sketch_size)))
        valid_p_idx = np.flatnonzero(paragraph_has_vector)
        p_sample_idx = valid_p_idx[_deterministic_sample_indices(int(valid_p_idx.size), sketch_k)]
        para_probe_vectors = (
            paragraph_vectors[p_sample_idx].copy()
            if p_sample_idx.size > 0
            else np.zeros((0, dimension), dtype=np.float32)
        )

        valid_e_idx = np.flatnonzero(node_has_vector)
        e_take = valid_e_idx[_deterministic_sample_indices(int(valid_e_idx.size), sketch_k)]
        entity_probe_vectors = (
            node_vectors[e_take].copy() if e_take.size > 0 else np.zeros((0, dimension), dtype=np.float32)
        )

        valid_r_idx = np.flatnonzero(relation_has_vector)
        r_take = valid_r_idx[_deterministic_sample_indices(int(valid_r_idx.size), sketch_k)]
        relation_probe_vectors = (
            relation_vectors[r_take].copy() if r_take.size > 0 else np.zeros((0, dimension), dtype=np.float32)
        )

        # 用句级参考探针（优先关系探针，回退段落/实体探针）计算各池一维 CSLS 偏置，匹配在线短句查询粒度
        query_like_probes = (
            relation_probe_vectors
            if relation_probe_vectors.shape[0] >= 4
            else (para_probe_vectors if para_probe_vectors.shape[0] >= 4 else entity_probe_vectors)
        )
        paragraph_csls_bias = _compute_csls_item_bias(paragraph_vectors, paragraph_has_vector, query_like_probes)
        node_csls_bias = _compute_csls_item_bias(node_vectors, node_has_vector, query_like_probes)
        relation_csls_bias = _compute_csls_item_bias(relation_vectors, relation_has_vector, query_like_probes)

        para_probe_csls_bias = (
            paragraph_csls_bias[p_sample_idx].copy() if p_sample_idx.size > 0 else np.zeros((0,), dtype=np.float32)
        )
        entity_probe_csls_bias = (
            node_csls_bias[e_take].copy() if e_take.size > 0 else np.zeros((0,), dtype=np.float32)
        )
        relation_probe_csls_bias = (
            relation_csls_bias[r_take].copy() if r_take.size > 0 else np.zeros((0,), dtype=np.float32)
        )

        # 构建实体/关系探针与段落的结构共现掩码，确保右尾偏度仅在真正无关的跨案例负样本对上统计
        e_probe_inc = np.zeros((int(e_take.size), num_paragraphs), dtype=bool)
        r_probe_inc = np.zeros((int(r_take.size), num_paragraphs), dtype=bool)
        if num_paragraphs > 0:
            pn_csc = paragraph_node_matrix.tocsc() if paragraph_node_matrix.nnz > 0 else None
            pr_csc = paragraph_relation_matrix.tocsc() if paragraph_relation_matrix.nnz > 0 else None
            for i_e, u_probe in enumerate(e_take):
                if pn_csc is not None:
                    u_col = pn_csc.getcol(int(u_probe))
                    if u_col.nnz > 0:
                        e_probe_inc[i_e, u_col.indices] = True
            for i_r, r_probe in enumerate(r_take):
                r_int = int(r_probe)
                if pr_csc is not None:
                    r_col = pr_csc.getcol(r_int)
                    if r_col.nnz > 0:
                        r_probe_inc[i_r, r_col.indices] = True
                if pn_csc is not None and r_int < relation_endpoints.shape[0]:
                    u_ep, v_ep = relation_endpoints[r_int]
                    if u_ep >= 0:
                        r_probe_inc[i_r, pn_csc.getcol(int(u_ep)).indices] = True
                    if v_ep >= 0:
                        r_probe_inc[i_r, pn_csc.getcol(int(v_ep)).indices] = True

        rp_cooccur = (
            r_probe_inc[:, p_sample_idx]
            if (r_take.size > 0 and p_sample_idx.size > 0)
            else np.zeros((int(r_take.size), int(p_sample_idx.size)), dtype=bool)
        )
        re_cooccur = (
            ((r_probe_inc.astype(np.int16) @ e_probe_inc.astype(np.int16).T) > 0)
            if (r_take.size > 0 and e_take.size > 0)
            else np.zeros((int(r_take.size), int(e_take.size)), dtype=bool)
        )
        ee_cooccur = (
            (np.eye(int(e_take.size), dtype=bool) | ((e_probe_inc.astype(np.int16) @ e_probe_inc.astype(np.int16).T) > 0))
            if e_take.size > 0
            else np.zeros((0, 0), dtype=bool)
        )
        rr_cooccur = (
            (np.eye(int(r_take.size), dtype=bool) | ((r_probe_inc.astype(np.int16) @ r_probe_inc.astype(np.int16).T) > 0))
            if r_take.size > 0
            else np.zeros((0, 0), dtype=bool)
        )

        (
            cone_baseline,
            para_tail_factor,
            entity_tail_factor,
            relation_tail_factor,
            query_cone_shift,
            fallback_para_null,
            fallback_entity_null,
            fallback_relation_null,
        ) = self._calibrate_fallback_nulls(
            para_probe_vectors=para_probe_vectors,
            para_probe_csls_bias=para_probe_csls_bias,
            entity_probe_vectors=entity_probe_vectors,
            entity_probe_csls_bias=entity_probe_csls_bias,
            relation_probe_vectors=relation_probe_vectors,
            relation_probe_csls_bias=relation_probe_csls_bias,
            rp_cooccur_mask=rp_cooccur,
            re_cooccur_mask=re_cooccur,
            ee_cooccur_mask=ee_cooccur,
            rr_cooccur_mask=rr_cooccur,
            csls_weight=float(self.config.csls_debias_weight),
            dimension=dimension,
        )

        # 6. 构建结构+模型自适应互近邻同义补边增强图与传播算子
        augmented_adj, norm_propagation, col_stochastic, dangling_mask = self._build_augmented_graph(
            raw_adj=raw_adj,
            node_names=node_names,
            node_vectors=node_vectors,
            node_has_vector=node_has_vector,
            node_csls_bias=node_csls_bias,
            node_grounded_paragraphs=node_grounded_paragraphs,
        )

        # 7. 图平滑表征 + 跨粒度子流形对齐 + 残差子空间尺度不变软分配球面 EM
        contextual_node_vecs, smoothed_para_vecs = self._compute_graph_smoothed_representations(
            paragraph_vectors=paragraph_vectors,
            paragraph_has_vector=paragraph_has_vector,
            node_vectors=node_vectors,
            node_has_vector=node_has_vector,
            raw_pn=raw_pn,
            norm_propagation=norm_propagation,
            dimension=dimension,
        )

        topic_smoothed_centroids, soft_para_responsibilities = self._discover_soft_topics(
            smoothed_para_vecs=smoothed_para_vecs,
            valid_mask=paragraph_has_vector,
        )
        topic_count = int(topic_smoothed_centroids.shape[0])

        # 8. 求解去偏话题重要性与跨子流形对齐的话题查询中心
        (
            global_pr,
            node_topic_importance,
            paragraph_topic_importance,
            topic_query_centroids,
            topic_summaries,
        ) = self._solve_topic_importance(
            topic_smoothed_centroids=topic_smoothed_centroids,
            soft_para_responsibilities=soft_para_responsibilities,
            paragraph_hashes=paragraph_hashes,
            paragraph_vectors=paragraph_vectors,
            paragraph_has_vector=paragraph_has_vector,
            paragraph_freshness=paragraph_freshness,
            smoothed_para_vecs=smoothed_para_vecs,
            node_names=node_names,
            node_vectors=node_vectors,
            node_has_vector=node_has_vector,
            contextual_node_vecs=contextual_node_vecs,
            node_idf=node_idf,
            relation_endpoints=relation_endpoints,
            relation_weights=relation_weights,
            relation_vectors=relation_vectors,
            relation_has_vector=relation_has_vector,
            paragraph_node_matrix=paragraph_node_matrix,
            paragraph_relation_matrix=paragraph_relation_matrix,
            col_stochastic=col_stochastic,
            dangling_mask=dangling_mask,
            query_cone_shift=query_cone_shift,
            dimension=dimension,
        )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.debug(
            f"构建紧凑话题图索引完成: paragraphs={num_paragraphs}, nodes={num_nodes}, "
            f"relations={num_relations}, topics={topic_count}, edges={augmented_adj.nnz}, "
            f"cone={cone_baseline:.3f}, tail=({para_tail_factor:.2f},{entity_tail_factor:.2f},{relation_tail_factor:.2f}), "
            f"elapsed_ms={elapsed_ms:.2f}"
        )

        return TopicImportanceIndex(
            cache_key=cache_key,
            built_at=time.time(),
            dimension=dimension,
            full_build_count=previous_full_builds + 1,
            incremental_update_count=0,
            base_paragraph_count=max(1, num_paragraphs),
            paragraph_hashes=paragraph_hashes,
            paragraph_hash_to_idx=paragraph_hash_to_idx,
            paragraph_timestamps=paragraph_timestamps,
            paragraph_freshness=paragraph_freshness,
            paragraph_csls_bias=paragraph_csls_bias,
            node_names=node_names,
            node_hashes=node_hashes,
            node_name_to_idx=node_name_to_idx,
            node_hash_to_idx=node_hash_to_idx,
            node_df=node_df,
            node_idf=node_idf,
            node_csls_bias=node_csls_bias,
            literal_matcher=literal_matcher,
            pattern_to_node_indices=pattern_to_node_indices,
            relation_hashes=relation_hashes,
            rel_hash_to_idx=rel_hash_to_idx,
            relation_endpoints=relation_endpoints,
            relation_weights=relation_weights,
            relation_csls_bias=relation_csls_bias,
            norm_propagation=norm_propagation,
            paragraph_node_matrix=paragraph_node_matrix,
            paragraph_relation_matrix=paragraph_relation_matrix,
            paragraph_relation_lists=paragraph_relation_lists,
            cone_baseline=cone_baseline,
            para_tail_factor=para_tail_factor,
            entity_tail_factor=entity_tail_factor,
            relation_tail_factor=relation_tail_factor,
            query_cone_shift=query_cone_shift,
            para_probe_vectors=para_probe_vectors,
            para_probe_csls_bias=para_probe_csls_bias,
            entity_probe_vectors=entity_probe_vectors,
            entity_probe_csls_bias=entity_probe_csls_bias,
            relation_probe_vectors=relation_probe_vectors,
            relation_probe_csls_bias=relation_probe_csls_bias,
            fallback_para_null=fallback_para_null,
            fallback_entity_null=fallback_entity_null,
            fallback_relation_null=fallback_relation_null,
            topic_count=topic_count,
            topic_smoothed_centroids=topic_smoothed_centroids,
            topic_query_centroids=topic_query_centroids,
            global_node_pagerank=global_pr,
            node_topic_importance=node_topic_importance,
            paragraph_topic_importance=paragraph_topic_importance,
            topic_summaries=topic_summaries,
        )

    def project_incremental(
        self,
        *,
        existing: TopicImportanceIndex,
        paragraph_vector_store: VectorStore,
        graph_vector_store: VectorStore,
        graph_snapshot: GraphSnapshot,
        metadata_store: MetadataStore,
        cache_key: Tuple[Any, ...],
    ) -> Optional[TopicImportanceIndex]:
        """对小批量新增记忆执行在线增量投影，不重跑 K-Means 与全图 PPR；若结构发生删除或大幅漂移则返回 None 交由全量重建。"""
        if existing.topic_count == 0:
            return None

        para_rows = metadata_store.query(
            """
            SELECT hash, LOWER(content) AS content_low,
                   COALESCE(event_time_end, event_time, updated_at, created_at, 0.0) AS eff_time
            FROM paragraphs
            WHERE (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY hash ASC
            """
        )
        current_hashes = [str(r["hash"]).strip() for r in para_rows if str(r["hash"]).strip()]
        current_set = set(current_hashes)
        old_set = set(existing.paragraph_hashes)

        # 若存在旧段落删除，返回 None 触发干净全量重建
        if not old_set.issubset(current_set):
            return None

        added_hashes = [h for h in current_hashes if h not in existing.paragraph_hash_to_idx]
        total_drift = len(current_hashes) - existing.base_paragraph_count
        max_allowed_drift = max(6, int(round(float(existing.base_paragraph_count) * float(self.config.incremental_drift_ratio))))
        if total_drift > max_allowed_drift:
            return None

        node_names = list(graph_snapshot.node_names)
        if len(node_names) < len(existing.node_names):
            return None
        # 校验前缀节点顺序未发生重排
        if node_names[: len(existing.node_names)] != existing.node_names:
            return None

        dimension = existing.dimension
        topic_count = existing.topic_count

        # 保持已有段落索引顺序不变，新段落追加在末尾，实现 O(ΔN) 向量读取与投影
        content_low_by_hash = {str(r["hash"]).strip(): str(r["content_low"] or "") for r in para_rows}
        time_by_hash = {str(r["hash"]).strip(): float(r["eff_time"] or 0.0) for r in para_rows}

        new_paragraph_hashes = [*existing.paragraph_hashes, *added_hashes]
        num_paragraphs = len(new_paragraph_hashes)
        paragraph_hash_to_idx = {h: i for i, h in enumerate(new_paragraph_hashes)}
        paragraph_timestamps = np.asarray([time_by_hash.get(h, 0.0) for h in new_paragraph_hashes], dtype=np.float64)
        paragraph_freshness = _compute_temporal_freshness(
            paragraph_timestamps,
            float(self.config.temporal_freshness_strength),
        )

        # 仅读取新增节点与新增段落的向量
        added_node_names = node_names[len(existing.node_names) :]
        num_nodes = len(node_names)
        node_name_to_idx = {name: i for i, name in enumerate(node_names)}
        node_hashes = [*existing.node_hashes, *[compute_hash(n) for n in added_node_names]]
        node_hash_to_idx = {h: i for i, h in enumerate(node_hashes)}

        pe_rows = (
            metadata_store.query(
                """
                SELECT pe.paragraph_hash, pe.entity_hash, COALESCE(pe.mention_count, 1) AS mention_count
                FROM paragraph_entities pe
                JOIN paragraphs p ON p.hash = pe.paragraph_hash
                WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
                """
            )
            if num_paragraphs > 0 and num_nodes > 0
            else []
        )
        node_df = np.zeros((num_nodes,), dtype=np.float32)
        pe_p_indices: List[int] = []
        pe_u_indices: List[int] = []
        pe_weights: List[float] = []
        for row in pe_rows:
            p_hash = str(row["paragraph_hash"])
            p_idx = paragraph_hash_to_idx.get(p_hash)
            u_idx = node_hash_to_idx.get(str(row["entity_hash"]))
            if p_idx is None or u_idx is None:
                continue
            mention_count = max(1, int(row["mention_count"]))
            is_grounded = bool(node_names[u_idx] and node_names[u_idx].lower() in content_low_by_hash.get(p_hash, ""))
            node_df[u_idx] += 1.0
            pe_p_indices.append(p_idx)
            pe_u_indices.append(u_idx)
            pe_weights.append(log(1.0 + float(mention_count)) * (1.0 if is_grounded else 0.55))

        node_idf = (np.log((float(num_paragraphs) + 1.0) / (node_df + 1.0)) + 1.0).astype(np.float32, copy=False)
        literal_matcher, pattern_to_node_indices = self._build_literal_matcher(
            node_names=node_names,
            node_df=node_df,
            num_paragraphs=num_paragraphs,
            metadata_store=metadata_store,
            node_name_to_idx=node_name_to_idx,
        )

        if pe_weights:
            weighted_vals = np.asarray(pe_weights, dtype=np.float32) * node_idf[np.asarray(pe_u_indices, dtype=np.int32)]
            raw_pn = coo_matrix(
                (weighted_vals, (pe_p_indices, pe_u_indices)),
                shape=(num_paragraphs, num_nodes),
                dtype=np.float32,
            ).tocsr()
            row_sums = np.asarray(raw_pn.sum(axis=1)).ravel()
            inv_row = np.zeros_like(row_sums, dtype=np.float32)
            nz = row_sums > 1e-8
            inv_row[nz] = 1.0 / np.sqrt(row_sums[nz])
            paragraph_node_matrix = (diags(inv_row) @ raw_pn).tocsr()
        else:
            paragraph_node_matrix = csr_matrix((num_paragraphs, num_nodes), dtype=np.float32)

        # 对新图的稀疏度归一化算子做快速更新（不重跑全量同义词搜索）
        sym_adj = ((graph_snapshot.raw_adj + graph_snapshot.raw_adj.T) * 0.5).tocsr()
        degrees = np.asarray(sym_adj.sum(axis=1)).ravel().astype(np.float32)
        inv_sqrt = np.zeros_like(degrees)
        nz_deg = degrees > 1e-8
        inv_sqrt[nz_deg] = 1.0 / np.sqrt(degrees[nz_deg])
        norm_propagation = (diags(inv_sqrt) @ sym_adj @ diags(inv_sqrt)).tocsr()

        # 扩展节点话题重要性：旧节点复用，新增节点通过邻域传播 + 已有话题中心投影获得重要性
        old_n = len(existing.node_names)
        node_topic_importance = np.zeros((num_nodes, topic_count), dtype=np.float32)
        node_csls_bias = np.zeros((num_nodes,), dtype=np.float32)
        global_pr = np.full((num_nodes,), 1.0 / float(max(1, num_nodes)), dtype=np.float32)
        if old_n > 0:
            node_topic_importance[:old_n] = existing.node_topic_importance
            node_csls_bias[:old_n] = existing.node_csls_bias
            global_pr[:old_n] = existing.global_node_pagerank

        if added_node_names:
            added_n_hashes = node_hashes[old_n:]
            added_n_vecs, added_n_valid = self._fetch_vectors_matrix(
                graph_vector_store,
                added_n_hashes,
                dimension,
                typed_prefix="entity:",
            )
            ref_g = (
                existing.relation_probe_vectors
                if existing.relation_probe_vectors.shape[0] >= 4
                else (
                    existing.para_probe_vectors
                    if existing.para_probe_vectors.shape[0] >= 4
                    else existing.entity_probe_vectors
                )
            )
            if ref_g.shape[0] >= 4 and np.any(added_n_valid):
                n_bg = np.quantile(added_n_vecs @ ref_g.T, 0.45, axis=1)
                node_csls_bias[old_n:] = np.where(
                    added_n_valid,
                    np.clip(n_bg - float(existing.fallback_entity_null[0]), -0.10, 0.14),
                    0.0,
                ).astype(np.float32)
            shifted_n, _ = _l2_normalize_rows(added_n_vecs - existing.query_cone_shift[None, :])
            aff = shifted_n @ existing.topic_query_centroids.T
            if topic_count > 1:
                med = np.clip(np.median(aff, axis=1, keepdims=True), 0.0, 0.88)
                sem_imp = np.maximum(0.0, (aff - med) / np.maximum(0.12, 1.0 - med))
            else:
                sem_imp = np.maximum(0.0, aff)
            sem_imp *= added_n_valid.astype(np.float32)[:, None]
            nbr_imp = (norm_propagation[old_n:, :old_n] @ existing.node_topic_importance) if old_n > 0 else 0.0
            new_imp = 0.55 * nbr_imp + 0.45 * sem_imp
            row_max = np.max(new_imp, axis=1, keepdims=True)
            node_topic_importance[old_n:] = np.where(row_max > 1e-8, np.clip(new_imp, 0.0, 1.0), 0.0)

        # 读取关系映射
        rel_rows = metadata_store.query(
            """
            SELECT hash, subject, predicate, object,
                   COALESCE(confidence, 1.0) AS confidence,
                   COALESCE(retention_strength, 1.0) AS retention_strength
            FROM relations
            WHERE (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY hash ASC
            """
        )
        relation_hashes: List[str] = []
        relation_endpoints_list: List[Tuple[int, int]] = []
        relation_weight_list: List[float] = []
        rel_hash_to_idx: Dict[str, int] = {}
        rel_triples: List[Tuple[str, str, str]] = []
        for row in rel_rows:
            r_hash = str(row["hash"]).strip()
            if not r_hash:
                continue
            subj = str(row["subject"] or "").strip().lower()
            pred = str(row["predicate"] or "").strip().lower()
            obj = str(row["object"] or "").strip().lower()
            conf = max(0.05, float(row["confidence"]))
            ret_s = max(0.20, min(1.25, float(row["retention_strength"])))
            rel_hash_to_idx[r_hash] = len(relation_hashes)
            relation_hashes.append(r_hash)
            rel_triples.append((subj, pred, obj))
            relation_endpoints_list.append((node_name_to_idx.get(subj, -1), node_name_to_idx.get(obj, -1)))
            relation_weight_list.append(conf * (0.75 + 0.25 * ret_s))

        num_relations = len(relation_hashes)
        relation_endpoints = (
            np.asarray(relation_endpoints_list, dtype=np.int32)
            if num_relations > 0
            else np.zeros((0, 2), dtype=np.int32)
        )
        relation_weights = (
            np.asarray(relation_weight_list, dtype=np.float32) if num_relations > 0 else np.zeros((0,), dtype=np.float32)
        )

        pr_rows = (
            metadata_store.query(
                """
                SELECT pr.paragraph_hash, pr.relation_hash
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE (p.is_deleted IS NULL OR p.is_deleted = 0)
                """
            )
            if num_paragraphs > 0 and num_relations > 0
            else []
        )
        pr_p: List[int] = []
        pr_r: List[int] = []
        pr_w: List[float] = []
        paragraph_relation_lists: List[List[Tuple[int, float]]] = [[] for _ in range(num_paragraphs)]
        for row in pr_rows:
            p_hash = str(row["paragraph_hash"])
            p_idx = paragraph_hash_to_idx.get(p_hash)
            r_idx = rel_hash_to_idx.get(str(row["relation_hash"]))
            if p_idx is None or r_idx is None:
                continue
            subj, pred, obj = rel_triples[r_idx]
            t_low = content_low_by_hash.get(p_hash, "")
            s_in = bool(subj and subj in t_low)
            o_in = bool(obj and obj in t_low)
            grounding = 1.0 if (s_in and o_in) else ((0.62 if (pred and pred in t_low) else 0.50) if (s_in or o_in) else 0.25)
            w_val = grounding * float(relation_weights[r_idx])
            pr_p.append(p_idx)
            pr_r.append(r_idx)
            pr_w.append(w_val)
            paragraph_relation_lists[p_idx].append((r_idx, w_val))

        paragraph_relation_matrix = (
            coo_matrix((np.asarray(pr_w, dtype=np.float32), (pr_p, pr_r)), shape=(num_paragraphs, num_relations), dtype=np.float32).tocsr()
            if pr_w
            else csr_matrix((num_paragraphs, num_relations), dtype=np.float32)
        )

        relation_csls_bias = np.zeros((num_relations,), dtype=np.float32)
        for idx_r, r_h in enumerate(relation_hashes):
            old_r_idx = existing.rel_hash_to_idx.get(r_h)
            if old_r_idx is not None:
                relation_csls_bias[idx_r] = existing.relation_csls_bias[old_r_idx]

        # 扩展段落话题重要性：旧段落按最新时效微调，新增段落在线投影到现有话题中心与节点重要性
        old_p = len(existing.paragraph_hashes)
        paragraph_topic_importance = np.zeros((num_paragraphs, topic_count), dtype=np.float32)
        paragraph_csls_bias = np.zeros((num_paragraphs,), dtype=np.float32)
        if old_p > 0:
            paragraph_csls_bias[:old_p] = existing.paragraph_csls_bias
            old_fresh_ratio = paragraph_freshness[:old_p] / np.maximum(1e-6, existing.paragraph_freshness)
            paragraph_topic_importance[:old_p] = np.clip(
                existing.paragraph_topic_importance * old_fresh_ratio[:, None],
                0.0,
                1.0,
            )

        if added_hashes:
            added_p_vecs, added_p_valid = self._fetch_vectors_matrix(
                paragraph_vector_store,
                added_hashes,
                dimension,
            )
            ref_p = (
                existing.relation_probe_vectors
                if existing.relation_probe_vectors.shape[0] >= 4
                else (
                    existing.para_probe_vectors
                    if existing.para_probe_vectors.shape[0] >= 4
                    else existing.entity_probe_vectors
                )
            )
            if ref_p.shape[0] >= 4 and np.any(added_p_valid):
                p_bg = np.quantile(added_p_vecs @ ref_p.T, 0.45, axis=1)
                paragraph_csls_bias[old_p:] = np.where(
                    added_p_valid,
                    np.clip(p_bg - float(existing.fallback_para_null[0]), -0.10, 0.14),
                    0.0,
                ).astype(np.float32)
            p_sem_raw = added_p_vecs @ existing.topic_smoothed_centroids.T
            if topic_count > 1:
                p_med = np.clip(np.median(p_sem_raw, axis=1, keepdims=True), 0.0, 0.88)
                p_sem = np.maximum(0.0, (p_sem_raw - p_med) / np.maximum(0.12, 1.0 - p_med))
            else:
                p_sem = np.maximum(0.0, p_sem_raw)
            p_sem *= added_p_valid.astype(np.float32)[:, None]
            p_graph = paragraph_node_matrix[old_p:] @ node_topic_importance if num_nodes > 0 else np.zeros_like(p_sem)
            g_max = np.max(p_graph, axis=1, keepdims=True)
            p_graph_norm = np.where(g_max > 1e-8, p_graph / np.maximum(g_max, 1e-8), 0.0)
            s_max = np.max(p_sem, axis=1, keepdims=True)
            p_sem_norm = np.where(s_max > 1e-8, p_sem / np.maximum(s_max, 1e-8), 0.0)
            ratio = float(self.config.paragraph_graph_importance_ratio)
            new_p_imp = (ratio * p_graph_norm + (1.0 - ratio) * p_sem_norm) * paragraph_freshness[old_p:, None]
            paragraph_topic_importance[old_p:] = np.clip(new_p_imp, 0.0, 1.0)

        return TopicImportanceIndex(
            cache_key=cache_key,
            built_at=time.time(),
            dimension=dimension,
            full_build_count=existing.full_build_count,
            incremental_update_count=existing.incremental_update_count + 1,
            base_paragraph_count=existing.base_paragraph_count,
            paragraph_hashes=new_paragraph_hashes,
            paragraph_hash_to_idx=paragraph_hash_to_idx,
            paragraph_timestamps=paragraph_timestamps,
            paragraph_freshness=paragraph_freshness,
            paragraph_csls_bias=paragraph_csls_bias,
            node_names=node_names,
            node_hashes=node_hashes,
            node_name_to_idx=node_name_to_idx,
            node_hash_to_idx=node_hash_to_idx,
            node_df=node_df,
            node_idf=node_idf,
            node_csls_bias=node_csls_bias,
            literal_matcher=literal_matcher,
            pattern_to_node_indices=pattern_to_node_indices,
            relation_hashes=relation_hashes,
            rel_hash_to_idx=rel_hash_to_idx,
            relation_endpoints=relation_endpoints,
            relation_weights=relation_weights,
            relation_csls_bias=relation_csls_bias,
            norm_propagation=norm_propagation,
            paragraph_node_matrix=paragraph_node_matrix,
            paragraph_relation_matrix=paragraph_relation_matrix,
            paragraph_relation_lists=paragraph_relation_lists,
            cone_baseline=existing.cone_baseline,
            para_tail_factor=existing.para_tail_factor,
            entity_tail_factor=existing.entity_tail_factor,
            relation_tail_factor=existing.relation_tail_factor,
            query_cone_shift=existing.query_cone_shift,
            para_probe_vectors=existing.para_probe_vectors,
            para_probe_csls_bias=existing.para_probe_csls_bias,
            entity_probe_vectors=existing.entity_probe_vectors,
            entity_probe_csls_bias=existing.entity_probe_csls_bias,
            relation_probe_vectors=existing.relation_probe_vectors,
            relation_probe_csls_bias=existing.relation_probe_csls_bias,
            fallback_para_null=existing.fallback_para_null,
            fallback_entity_null=existing.fallback_entity_null,
            fallback_relation_null=existing.fallback_relation_null,
            topic_count=topic_count,
            topic_smoothed_centroids=existing.topic_smoothed_centroids,
            topic_query_centroids=existing.topic_query_centroids,
            global_node_pagerank=global_pr,
            node_topic_importance=node_topic_importance,
            paragraph_topic_importance=paragraph_topic_importance,
            topic_summaries=existing.topic_summaries,
        )

    @staticmethod
    def _calibrate_fallback_nulls(
        *,
        para_probe_vectors: np.ndarray,
        para_probe_csls_bias: np.ndarray,
        entity_probe_vectors: np.ndarray,
        entity_probe_csls_bias: np.ndarray,
        relation_probe_vectors: np.ndarray,
        relation_probe_csls_bias: np.ndarray,
        rp_cooccur_mask: np.ndarray,
        re_cooccur_mask: np.ndarray,
        ee_cooccur_mask: np.ndarray,
        rr_cooccur_mask: np.ndarray,
        csls_weight: float,
        dimension: int,
    ) -> Tuple[
        float,
        float,
        float,
        float,
        np.ndarray,
        Tuple[float, float],
        Tuple[float, float],
        Tuple[float, float],
    ]:
        """从段落/实体/关系三池同质探针草图分别校准公共锥基线、右尾偏度因子 λ_tail 与后备零分布参数。"""
        if para_probe_vectors.shape[0] >= 4:
            mean_p = np.mean(para_probe_vectors, axis=0)
            cone = float(np.clip(np.dot(mean_p, mean_p), 0.0, 0.88))
        else:
            cone = 0.0

        query_ref = (
            entity_probe_vectors
            if entity_probe_vectors.shape[0] >= 4
            else (relation_probe_vectors if relation_probe_vectors.shape[0] >= 4 else para_probe_vectors)
        )
        if query_ref.shape[0] >= 4:
            mean_q = np.mean(query_ref, axis=0)
            q_cone_sq = float(np.dot(mean_q, mean_q))
            query_cone_shift = (0.88 * mean_q).astype(np.float32) if q_cone_sq > 0.14 else np.zeros((dimension,), dtype=np.float32)
        else:
            query_cone_shift = np.zeros((dimension,), dtype=np.float32)

        cw = float(csls_weight)

        # 1. 段落池后备零分布与右尾因子（优先用句级关系探针模拟聊天句查询）
        if relation_probe_vectors.shape[0] >= 4 and para_probe_vectors.shape[0] >= 4:
            rp = relation_probe_vectors @ para_probe_vectors.T
            if para_probe_csls_bias.shape[0] == para_probe_vectors.shape[0] and cw > 0.0:
                rp = rp - cw * para_probe_csls_bias[None, :]
            unrel_rp = rp[~rp_cooccur_mask] if rp_cooccur_mask.shape == rp.shape else rp.ravel()
            p_mu, p_std, p_tail = _estimate_tail_skewness_factor(unrel_rp if unrel_rp.size >= 8 else rp)
        elif para_probe_vectors.shape[0] >= 4:
            pp = para_probe_vectors @ para_probe_vectors.T
            if para_probe_csls_bias.shape[0] == para_probe_vectors.shape[0] and cw > 0.0:
                pp = pp - cw * para_probe_csls_bias[None, :]
            off_p = pp[~np.eye(pp.shape[0], dtype=bool)]
            p_mu, p_std, p_tail = _estimate_tail_skewness_factor(off_p)
        else:
            p_mu, p_std, p_tail = 0.0, 0.08, 1.0

        # 2. 实体池后备零分布与右尾因子（同质名词粒度，不混入关系陈述）
        if relation_probe_vectors.shape[0] >= 4 and entity_probe_vectors.shape[0] >= 4:
            re_mat = relation_probe_vectors @ entity_probe_vectors.T
            if entity_probe_csls_bias.shape[0] == entity_probe_vectors.shape[0] and cw > 0.0:
                re_mat = re_mat - cw * entity_probe_csls_bias[None, :]
            unrel_re = re_mat[~re_cooccur_mask] if re_cooccur_mask.shape == re_mat.shape else re_mat.ravel()
            e_mu, e_std, e_tail = _estimate_tail_skewness_factor(unrel_re if unrel_re.size >= 8 else re_mat)
        elif entity_probe_vectors.shape[0] >= 4:
            ee = entity_probe_vectors @ entity_probe_vectors.T
            if entity_probe_csls_bias.shape[0] == entity_probe_vectors.shape[0] and cw > 0.0:
                ee = ee - cw * entity_probe_csls_bias[None, :]
            unrel_ee = ee[~ee_cooccur_mask] if ee_cooccur_mask.shape == ee.shape else ee[~np.eye(ee.shape[0], dtype=bool)]
            fb_ee = ee[~np.eye(ee.shape[0], dtype=bool)]
            e_mu, e_std, e_tail = _estimate_tail_skewness_factor(unrel_ee if unrel_ee.size >= 8 else fb_ee)
        else:
            e_mu, e_std, e_tail = p_mu, p_std, p_tail

        # 3. 关系池后备零分布与右尾因子（同质主谓宾陈述粒度）
        if relation_probe_vectors.shape[0] >= 4:
            rr = relation_probe_vectors @ relation_probe_vectors.T
            if relation_probe_csls_bias.shape[0] == relation_probe_vectors.shape[0] and cw > 0.0:
                rr = rr - cw * relation_probe_csls_bias[None, :]
            unrel_rr = rr[~rr_cooccur_mask] if rr_cooccur_mask.shape == rr.shape else rr[~np.eye(rr.shape[0], dtype=bool)]
            fb_rr = rr[~np.eye(rr.shape[0], dtype=bool)]
            r_mu, r_std, r_tail = _estimate_tail_skewness_factor(unrel_rr if unrel_rr.size >= 8 else fb_rr)
        else:
            r_mu, r_std, r_tail = e_mu, e_std, e_tail

        return (
            cone,
            p_tail,
            e_tail,
            r_tail,
            query_cone_shift,
            (p_mu, p_std),
            (e_mu, e_std),
            (r_mu, r_std),
        )

    def _build_augmented_graph(
        self,
        *,
        raw_adj: csr_matrix,
        node_names: List[str],
        node_vectors: np.ndarray,
        node_has_vector: np.ndarray,
        node_csls_bias: np.ndarray,
        node_grounded_paragraphs: List[Set[int]],
    ) -> Tuple[csr_matrix, csr_matrix, csr_matrix, np.ndarray]:
        """构建锚定加权的结构 + 互近邻 (Mutual k-NN) 模型自适应 Z-Score 同义补边增强图。"""
        num_nodes = int(raw_adj.shape[0])
        if num_nodes == 0:
            empty = csr_matrix((0, 0), dtype=np.float32)
            return empty, empty, empty, np.zeros((0,), dtype=bool)

        sym_struct = ((raw_adj + raw_adj.T) * 0.5).tocoo()
        rows_list: List[int] = []
        cols_list: List[int] = []
        vals_list: List[float] = []
        existing_struct_pairs: Set[Tuple[int, int]] = set()

        for u, v, w in zip(sym_struct.row, sym_struct.col, sym_struct.data, strict=True):
            if u == v or w <= 0.0:
                continue
            u_int = int(u)
            v_int = int(v)
            existing_struct_pairs.add((u_int, v_int))
            shared_grounded = bool(node_grounded_paragraphs[u_int] & node_grounded_paragraphs[v_int])
            either_grounded = bool(node_grounded_paragraphs[u_int] or node_grounded_paragraphs[v_int])
            edge_reliability = 1.0 if shared_grounded else (0.55 if either_grounded else 0.30)
            rows_list.append(u_int)
            cols_list.append(v_int)
            vals_list.append(float(w) * edge_reliability)

        if self.config.enable_synonym_edges:
            valid_indices = np.flatnonzero(node_has_vector)
            if valid_indices.size >= 3:
                valid_vecs = np.ascontiguousarray(node_vectors[valid_indices], dtype=np.float32)
                local_biases = node_csls_bias[valid_indices]

                csls_w = float(self.config.csls_debias_weight)

                # 采样无关实体对背景余弦分布，使平坦低余弦模型与高余弦紧凑模型拥有一致的同义判别力
                sample_locs = _deterministic_sample_indices(int(valid_indices.size), 64)
                e_sample = valid_vecs[sample_locs]
                if e_sample.shape[0] >= 4:
                    s_biases = local_biases[sample_locs]
                    ee = (e_sample @ e_sample.T) - 0.50 * csls_w * (s_biases[:, None] + s_biases[None, :])
                    ee_mask = np.eye(e_sample.shape[0], dtype=bool)
                    for si in range(e_sample.shape[0]):
                        ui = int(valid_indices[int(sample_locs[si])])
                        for sj in range(si + 1, e_sample.shape[0]):
                            uj = int(valid_indices[int(sample_locs[sj])])
                            if (ui, uj) in existing_struct_pairs or bool(
                                node_grounded_paragraphs[ui] & node_grounded_paragraphs[uj]
                            ):
                                ee_mask[si, sj] = True
                                ee_mask[sj, si] = True
                    unrelated_ee = ee[~ee_mask]
                    fallback_ee = ee[~np.eye(ee.shape[0], dtype=bool)]
                    e_mu, e_std, e_tail = _estimate_tail_skewness_factor(
                        unrelated_ee if unrelated_ee.size >= 8 else fallback_ee
                    )
                else:
                    e_mu, e_std, e_tail = 0.0, 0.08, 1.0

                base_mu = max(0.0, e_mu)
                cone_denom = max(0.14, 1.0 - base_mu)
                # 自适应同义门限：平坦模型上随分布标准差自动下探（最低 0.30），紧凑重尾模型上保持严格
                adaptive_sim_floor = min(
                    float(self.config.synonym_similarity_threshold),
                    max(0.30, (2.85 * e_tail * e_std) / cone_denom),
                )
                z_syn_floor = max(2.65 * e_tail, 1.02 * _evt_noise_ceiling(int(valid_indices.size), 0.92))

                top_k = min(int(self.config.synonym_top_k) + 1, int(valid_indices.size))
                syn_weight = float(self.config.synonym_edge_weight)

                flat_index = faiss.IndexFlatIP(int(valid_vecs.shape[1]))
                flat_index.add(valid_vecs)
                sims, nbrs = flat_index.search(valid_vecs, top_k)

                nbr_sets: List[Set[int]] = [
                    {int(nj) for nj in nbrs[li] if nj >= 0 and int(nj) != li}
                    for li in range(valid_indices.size)
                ]

                for local_i in range(valid_indices.size):
                    u_int = int(valid_indices[local_i])
                    name_u = node_names[u_int]
                    for sim_val, local_j_raw in zip(sims[local_i], nbrs[local_i], strict=True):
                        local_j = int(local_j_raw)
                        if local_j <= local_i or local_j < 0:
                            continue
                        # 强制互为 Top-K 近邻（Mutual k-NN），消除向量空间 Hub 节点的单向放射伪边
                        if local_i not in nbr_sets[local_j]:
                            continue
                        v_int = int(valid_indices[local_j])
                        if (u_int, v_int) in existing_struct_pairs:
                            continue
                        if _has_conflicting_slot_tokens(name_u, node_names[v_int]):
                            continue

                        csls_sim = float(sim_val) - 0.50 * csls_w * float(local_biases[local_i] + local_biases[local_j])
                        z_syn = (csls_sim - e_mu) / e_std
                        adj_sim = (csls_sim - base_mu) / cone_denom
                        if z_syn < z_syn_floor or adj_sim < adaptive_sim_floor:
                            continue

                        w_syn = min(1.0, adj_sim) * syn_weight
                        rows_list.extend((u_int, v_int))
                        cols_list.extend((v_int, u_int))
                        vals_list.extend((w_syn, w_syn))

        if rows_list:
            augmented_adj = coo_matrix(
                (np.asarray(vals_list, dtype=np.float32), (rows_list, cols_list)),
                shape=(num_nodes, num_nodes),
                dtype=np.float32,
            ).tocsr()
        else:
            augmented_adj = csr_matrix((num_nodes, num_nodes), dtype=np.float32)

        degrees = np.asarray(augmented_adj.sum(axis=1)).ravel().astype(np.float32)
        dangling_mask = degrees <= 1e-8

        inv_sqrt_deg = np.zeros_like(degrees, dtype=np.float32)
        inv_deg = np.zeros_like(degrees, dtype=np.float32)
        active = ~dangling_mask
        inv_sqrt_deg[active] = 1.0 / np.sqrt(degrees[active])
        inv_deg[active] = 1.0 / degrees[active]

        d_inv_sqrt = diags(inv_sqrt_deg)
        norm_propagation = (d_inv_sqrt @ augmented_adj @ d_inv_sqrt).tocsr()
        col_stochastic = (augmented_adj @ diags(inv_deg)).tocsr()
        return augmented_adj, norm_propagation, col_stochastic, dangling_mask

    def _compute_graph_smoothed_representations(
        self,
        *,
        paragraph_vectors: np.ndarray,
        paragraph_has_vector: np.ndarray,
        node_vectors: np.ndarray,
        node_has_vector: np.ndarray,
        raw_pn: csr_matrix,
        norm_propagation: csr_matrix,
        dimension: int,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """融合实体语义与图邻域上下文，生成平滑节点表征与平滑段落表征。"""
        num_nodes = int(node_vectors.shape[0])
        num_paragraphs = int(paragraph_vectors.shape[0])

        if num_nodes == 0:
            return np.zeros((0, dimension), dtype=np.float32), paragraph_vectors.copy()

        # 若嵌入模型在长段落与短实体之间存在显著子流形中心偏移（非对称模型），先消除偏移差再双向平滑
        has_p_cone = int(np.sum(paragraph_has_vector)) >= 4
        has_n_cone = int(np.sum(node_has_vector)) >= 4
        mean_p = np.mean(paragraph_vectors[paragraph_has_vector], axis=0) if has_p_cone else np.zeros((dimension,), dtype=np.float32)
        mean_n = np.mean(node_vectors[node_has_vector], axis=0) if has_n_cone else np.zeros((dimension,), dtype=np.float32)
        p_to_n_shift = (0.85 * (mean_p - mean_n)).astype(np.float32) if (has_p_cone and has_n_cone and float(np.linalg.norm(mean_p - mean_n)) > 0.16) else None

        if num_paragraphs > 0 and raw_pn.nnz > 0:
            col_sums = np.asarray(raw_pn.sum(axis=0)).ravel().astype(np.float32)
            inv_col = np.zeros_like(col_sums)
            valid_cols = col_sums > 1e-8
            inv_col[valid_cols] = 1.0 / col_sums[valid_cols]
            aligned_p = (paragraph_vectors - p_to_n_shift[None, :]) if p_to_n_shift is not None else paragraph_vectors
            node_from_para = (diags(inv_col) @ raw_pn.T) @ aligned_p
            node_from_para, has_para_ctx = _l2_normalize_rows(node_from_para)
        else:
            node_from_para = np.zeros((num_nodes, dimension), dtype=np.float32)
            has_para_ctx = np.zeros((num_nodes,), dtype=bool)

        contextual = np.zeros((num_nodes, dimension), dtype=np.float32)
        both = node_has_vector & has_para_ctx
        only_node = node_has_vector & (~has_para_ctx)
        only_para = (~node_has_vector) & has_para_ctx
        contextual[both] = 0.60 * node_vectors[both] + 0.40 * node_from_para[both]
        contextual[only_node] = node_vectors[only_node]
        contextual[only_para] = node_from_para[only_para]
        contextual, _ = _l2_normalize_rows(contextual)

        if norm_propagation.nnz > 0:
            lam = float(self.config.node_smoothing_lambda)
            neighbor_ctx = norm_propagation @ contextual
            contextual = (1.0 - lam) * contextual + lam * neighbor_ctx
            contextual, _ = _l2_normalize_rows(contextual)

        if num_paragraphs == 0 or raw_pn.nnz == 0:
            return contextual, paragraph_vectors.copy()

        row_sums = np.asarray(raw_pn.sum(axis=1)).ravel().astype(np.float32)
        inv_row = np.zeros_like(row_sums)
        valid_rows = row_sums > 1e-8
        inv_row[valid_rows] = 1.0 / row_sums[valid_rows]
        aligned_ctx = (contextual + p_to_n_shift[None, :]) if p_to_n_shift is not None else contextual
        para_graph_ctx = (diags(inv_row) @ raw_pn) @ aligned_ctx
        para_graph_ctx, has_graph_ctx = _l2_normalize_rows(para_graph_ctx)

        beta = float(self.config.graph_smoothing_beta)
        smoothed_para = paragraph_vectors.copy()
        blend_mask = paragraph_has_vector & has_graph_ctx
        smoothed_para[blend_mask] = (1.0 - beta) * paragraph_vectors[blend_mask] + beta * para_graph_ctx[blend_mask]
        only_graph_mask = (~paragraph_has_vector) & has_graph_ctx
        smoothed_para[only_graph_mask] = para_graph_ctx[only_graph_mask]
        smoothed_para, _ = _l2_normalize_rows(smoothed_para)
        return contextual, smoothed_para

    def _discover_soft_topics(
        self,
        *,
        smoothed_para_vecs: np.ndarray,
        valid_mask: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """在去均值语义残差子空间执行软分配球面 EM（Soft vMF Clustering），支持一话多题软归属。"""
        num_paragraphs, dimension = smoothed_para_vecs.shape
        if num_paragraphs == 0:
            return np.zeros((0, dimension), dtype=np.float32), np.zeros((0, 0), dtype=np.float32)

        valid_indices = np.flatnonzero(valid_mask)
        if valid_indices.size == 0:
            centroid = np.zeros((1, dimension), dtype=np.float32)
            resp = np.ones((num_paragraphs, 1), dtype=np.float32)
            return centroid, resp

        n_valid = int(valid_indices.size)
        if n_valid <= 2:
            k_target = n_valid
        else:
            scaled = int(round(sqrt(float(n_valid) * float(self.config.topic_count_scale))))
            k_target = min(n_valid, max(int(self.config.min_topics), min(int(self.config.max_topics), scaled)))

        valid_vecs = smoothed_para_vecs[valid_indices]
        raw_mean = np.mean(valid_vecs, axis=0, keepdims=True)
        cone_norm_sq = float(np.dot(raw_mean[0], raw_mean[0]))
        if n_valid >= 4 and cone_norm_sq > 0.20:
            cluster_all_vecs, _ = _l2_normalize_rows(smoothed_para_vecs - 0.88 * raw_mean)
            cluster_valid_vecs = cluster_all_vecs[valid_indices]
        else:
            cluster_all_vecs = smoothed_para_vecs
            cluster_valid_vecs = valid_vecs

        # 确定性最远点初始化
        mean_vec = np.mean(cluster_valid_vecs, axis=0, keepdims=True)
        mean_vec, _ = _l2_normalize_rows(mean_vec)
        first_local = int(np.argmax(cluster_valid_vecs @ mean_vec[0]))
        chosen_locals: List[int] = [first_local]
        min_dist = 1.0 - (cluster_valid_vecs @ cluster_valid_vecs[first_local])

        for _ in range(1, k_target):
            next_local = int(np.argmax(min_dist))
            if float(min_dist[next_local]) <= 1e-5:
                break
            chosen_locals.append(next_local)
            dist_to_new = 1.0 - (cluster_valid_vecs @ cluster_valid_vecs[next_local])
            min_dist = np.minimum(min_dist, dist_to_new)

        centroids = cluster_valid_vecs[np.asarray(chosen_locals, dtype=np.int32)].copy()
        centroids, _ = _l2_normalize_rows(centroids)

        base_temp = max(0.04, float(self.config.soft_topic_temperature))
        prev_centroids = centroids.copy()

        # 尺度自适应软分配球面 EM 迭代：按当前模型的簇间余弦离散度缩放温度，使平坦/紧凑模型簇熵一致
        for _ in range(max(1, int(self.config.kmeans_iterations))):
            sims = cluster_valid_vecs @ centroids.T  # (n_valid, K)
            sim_spread = float(np.std(sims)) if sims.shape[1] >= 2 else 0.16
            eff_temp = max(0.025, base_temp * max(0.30, min(2.20, sim_spread / 0.16)))
            shifted = (sims - np.max(sims, axis=1, keepdims=True)) / eff_temp
            weights = np.exp(np.clip(shifted, -30.0, 0.0))
            resp_valid = weights / np.maximum(1e-8, np.sum(weights, axis=1, keepdims=True))

            # 过滤几乎无样本承担的退化空簇
            mass = np.sum(resp_valid, axis=0)
            keep_cols = mass > 0.25
            if not np.any(keep_cols):
                keep_cols[int(np.argmax(mass))] = True
            resp_kept = resp_valid[:, keep_cols]
            new_c = resp_kept.T @ cluster_valid_vecs
            new_c, _ = _l2_normalize_rows(new_c)
            centroids = new_c
            if centroids.shape == prev_centroids.shape and float(np.max(np.abs(centroids - prev_centroids))) < 1e-4:
                break
            prev_centroids = centroids.copy()

        # 计算全量段落的软话题归属责任矩阵 R \in R^{N_P x K}
        all_sims = cluster_all_vecs @ centroids.T
        all_spread = float(np.std(all_sims[valid_mask])) if (all_sims.shape[1] >= 2 and np.any(valid_mask)) else 0.16
        final_temp = max(0.025, base_temp * max(0.30, min(2.20, all_spread / 0.16)))
        shifted_all = (all_sims - np.max(all_sims, axis=1, keepdims=True)) / final_temp
        all_w = np.exp(np.clip(shifted_all, -30.0, 0.0))
        soft_resp = (all_w / np.maximum(1e-8, np.sum(all_w, axis=1, keepdims=True))).astype(np.float32)
        if np.any(~valid_mask):
            soft_resp[~valid_mask] = 1.0 / float(max(1, centroids.shape[0]))

        # 在平滑段落原空间按软责任加权回算标准话题中心
        weighted_valid_resp = soft_resp * valid_mask.astype(np.float32)[:, None]
        raw_topic_centroids = weighted_valid_resp.T @ smoothed_para_vecs
        raw_topic_centroids, c_valid = _l2_normalize_rows(raw_topic_centroids)
        for cid in range(raw_topic_centroids.shape[0]):
            if not c_valid[cid]:
                raw_topic_centroids[cid] = centroids[cid]

        return raw_topic_centroids, soft_resp

    def _solve_topic_importance(
        self,
        *,
        topic_smoothed_centroids: np.ndarray,
        soft_para_responsibilities: np.ndarray,
        paragraph_hashes: List[str],
        paragraph_vectors: np.ndarray,
        paragraph_has_vector: np.ndarray,
        paragraph_freshness: np.ndarray,
        smoothed_para_vecs: np.ndarray,
        node_names: List[str],
        node_vectors: np.ndarray,
        node_has_vector: np.ndarray,
        contextual_node_vecs: np.ndarray,
        node_idf: np.ndarray,
        relation_endpoints: np.ndarray,
        relation_weights: np.ndarray,
        relation_vectors: np.ndarray,
        relation_has_vector: np.ndarray,
        paragraph_node_matrix: csr_matrix,
        paragraph_relation_matrix: csr_matrix,
        col_stochastic: csr_matrix,
        dangling_mask: np.ndarray,
        query_cone_shift: np.ndarray,
        dimension: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[TopicClusterSummary]]:
        """批量求解全局 PageRank 与各话题个性化 PageRank，完成去偏节点重要性与时效感知段落重要性投影。"""
        num_paragraphs = len(paragraph_hashes)
        num_nodes = len(node_names)
        topic_count = int(topic_smoothed_centroids.shape[0])

        if topic_count == 0 or num_paragraphs == 0:
            return (
                np.zeros((num_nodes,), dtype=np.float32),
                np.zeros((num_nodes, 0), dtype=np.float32),
                np.zeros((num_paragraphs, 0), dtype=np.float32),
                np.zeros((0, dimension), dtype=np.float32),
                [],
            )

        # 1. 结合软话题段落支撑与语义亲和求解节点个性化种子分布，并跑单次矩阵化 PPR
        if num_nodes > 0:
            raw_affinity = contextual_node_vecs @ topic_smoothed_centroids.T
            if topic_count > 1:
                node_base_aff = np.maximum(0.0, np.median(raw_affinity, axis=1, keepdims=True))
                node_mean_pos = np.mean(np.maximum(0.0, raw_affinity - node_base_aff), axis=1, keepdims=True)
                spec_affinity = np.maximum(0.0, (raw_affinity - node_base_aff) - 0.50 * node_mean_pos)
            else:
                spec_affinity = np.maximum(0.0, raw_affinity)

            # 融合段落软归属向节点的二部投影，使正交维细粒度节点也能获得所属话题簇种子
            if paragraph_node_matrix.nnz > 0:
                bipartite_node_resp = np.asarray(paragraph_node_matrix.T @ soft_para_responsibilities)
                if topic_count > 1:
                    b_med = np.median(bipartite_node_resp, axis=1, keepdims=True)
                    bipartite_spec = np.maximum(0.0, bipartite_node_resp - b_med)
                else:
                    bipartite_spec = np.maximum(0.0, bipartite_node_resp)
                b_max = np.max(bipartite_spec, axis=0, keepdims=True)
                bipartite_norm = np.where(b_max > 1e-8, bipartite_spec / np.maximum(b_max, 1e-8), 0.0)
            else:
                bipartite_norm = np.zeros((num_nodes, topic_count), dtype=np.float32)

            seed_matrix = (np.square(spec_affinity) + 0.35 * np.square(bipartite_norm)) * node_idf[:, None]
            for t_idx in range(topic_count):
                col_sum = float(np.sum(seed_matrix[:, t_idx]))
                if col_sum <= 1e-10:
                    fallback_col = np.square(np.maximum(0.0, raw_affinity[:, t_idx])) * node_idf
                    fb_sum = float(np.sum(fallback_col))
                    seed_matrix[:, t_idx] = (fallback_col / fb_sum) if fb_sum > 1e-10 else (1.0 / float(num_nodes))
                else:
                    seed_matrix[:, t_idx] /= col_sum

            uniform_col = np.full((num_nodes, 1), 1.0 / float(num_nodes), dtype=np.float32)
            personalization = np.hstack([uniform_col, seed_matrix.astype(np.float32, copy=False)])
            pi = personalization.copy()
            damping = float(self.config.ppr_damping)
            tol = float(self.config.ppr_tolerance)

            for _ in range(max(1, int(self.config.ppr_max_iterations))):
                dangling_mass = np.sum(pi[dangling_mask, :], axis=0, keepdims=True) if np.any(dangling_mask) else 0.0
                walk_term = col_stochastic @ pi + personalization * dangling_mass
                pi_next = damping * walk_term + (1.0 - damping) * personalization
                diff = float(np.max(np.sum(np.abs(pi_next - pi), axis=0)))
                pi = pi_next
                if diff < tol:
                    break

            global_pr = np.maximum(pi[:, 0], 1e-12).astype(np.float32)
            topic_ppr = np.maximum(pi[:, 1:], 0.0).astype(np.float32)

            gamma = float(self.config.hub_debias_gamma)
            denom = np.power(global_pr, gamma)[:, None] + 1e-6
            node_topic_importance = (topic_ppr / denom) * node_idf[:, None]
            col_max = np.max(node_topic_importance, axis=0, keepdims=True)
            valid_cols = col_max > 1e-10
            node_topic_importance = np.where(valid_cols, node_topic_importance / np.maximum(col_max, 1e-10), 0.0).astype(
                np.float32
            )
        else:
            global_pr = np.zeros((0,), dtype=np.float32)
            node_topic_importance = np.zeros((0, topic_count), dtype=np.float32)

        # 2. 在去锥残差共享空间构建查询路由中心（消除短查询/实体与长段落在非对称模型下的流形偏移）
        topic_query_centroids = np.zeros((topic_count, dimension), dtype=np.float32)
        valid_p_float = paragraph_has_vector.astype(np.float32)
        if int(np.sum(paragraph_has_vector)) >= 4:
            p_mean = np.mean(paragraph_vectors[paragraph_has_vector], axis=0)
            p_cone_shift = (0.88 * p_mean) if float(np.dot(p_mean, p_mean)) > 0.14 else np.zeros((dimension,), dtype=np.float32)
        else:
            p_cone_shift = np.zeros((dimension,), dtype=np.float32)

        res_p_vecs, _ = _l2_normalize_rows(paragraph_vectors - p_cone_shift[None, :])
        res_n_vecs, _ = _l2_normalize_rows(node_vectors - query_cone_shift[None, :]) if num_nodes > 0 else (node_vectors, node_has_vector)

        for t_idx in range(topic_count):
            resp_w = soft_para_responsibilities[:, t_idx] * valid_p_float
            w_p_sum = float(np.sum(resp_w))
            acc = np.zeros((dimension,), dtype=np.float32)
            if w_p_sum > 1e-8:
                acc += np.sum(res_p_vecs * (resp_w[:, None] / w_p_sum), axis=0)
            if num_nodes > 0 and np.any(node_has_vector):
                node_weights = node_topic_importance[:, t_idx] * node_has_vector.astype(np.float32)
                top_n = min(8, num_nodes)
                top_idx = np.argsort(-node_weights)[:top_n]
                top_w = node_weights[top_idx]
                w_sum = float(np.sum(top_w))
                if w_sum > 1e-8:
                    acc += 0.50 * np.sum(res_n_vecs[top_idx] * (top_w[:, None] / w_sum), axis=0)
            norm_acc = float(np.linalg.norm(acc))
            topic_query_centroids[t_idx] = (acc / norm_acc) if norm_acc > 1e-8 else topic_smoothed_centroids[t_idx]

        # 3. 关系话题重要性与时效加权段落话题重要性 I(m | t)
        num_relations = int(relation_endpoints.shape[0])
        if num_relations > 0 and num_nodes > 0:
            u_idx = relation_endpoints[:, 0]
            v_idx = relation_endpoints[:, 1]
            u_imp = np.where(u_idx[:, None] >= 0, node_topic_importance[np.maximum(u_idx, 0)], 0.0)
            v_imp = np.where(v_idx[:, None] >= 0, node_topic_importance[np.maximum(v_idx, 0)], 0.0)
            endpoint_imp = 0.5 * (u_imp + v_imp)
            if np.any(relation_has_vector):
                res_r_vecs, _ = _l2_normalize_rows(relation_vectors - query_cone_shift[None, :])
                raw_rel_sem = res_r_vecs @ topic_query_centroids.T
                if topic_count > 1:
                    rel_base = np.clip(np.median(raw_rel_sem, axis=1, keepdims=True), 0.0, 0.88)
                    rel_sem = np.maximum(0.0, (raw_rel_sem - rel_base) / np.maximum(0.12, 1.0 - rel_base))
                else:
                    rel_sem = np.maximum(0.0, raw_rel_sem)
                rel_topic_imp = (0.65 * endpoint_imp + 0.35 * rel_sem) * relation_weights[:, None]
            else:
                rel_topic_imp = endpoint_imp * relation_weights[:, None]
        else:
            rel_topic_imp = np.zeros((num_relations, topic_count), dtype=np.float32)

        para_graph_imp = np.zeros((num_paragraphs, topic_count), dtype=np.float32)
        if num_nodes > 0 and paragraph_node_matrix.nnz > 0:
            para_graph_imp += paragraph_node_matrix @ node_topic_importance
        if num_relations > 0 and paragraph_relation_matrix.nnz > 0:
            pr_row_sums = np.asarray(paragraph_relation_matrix.sum(axis=1)).ravel().astype(np.float32)
            inv_pr = np.zeros_like(pr_row_sums)
            nz = pr_row_sums > 1e-8
            inv_pr[nz] = 1.0 / np.sqrt(pr_row_sums[nz])
            para_graph_imp += 0.65 * ((diags(inv_pr) @ paragraph_relation_matrix) @ rel_topic_imp)

        pg_max = np.max(para_graph_imp, axis=0, keepdims=True)
        para_graph_norm = np.where(pg_max > 1e-10, para_graph_imp / np.maximum(pg_max, 1e-10), 0.0)

        raw_para_sem = smoothed_para_vecs @ topic_smoothed_centroids.T
        if topic_count > 1:
            para_base_sem = np.clip(np.median(raw_para_sem, axis=1, keepdims=True), 0.0, 0.88)
            para_sem_imp = np.maximum(0.0, (raw_para_sem - para_base_sem) / np.maximum(0.12, 1.0 - para_base_sem))
        else:
            para_sem_imp = np.maximum(0.0, raw_para_sem)
        # 融合软话题责任分布，确保跨多话题段落的次主导话题同样保有连续权重
        para_sem_imp = 0.65 * para_sem_imp + 0.35 * soft_para_responsibilities
        ps_max = np.max(para_sem_imp, axis=0, keepdims=True)
        para_sem_norm = np.where(ps_max > 1e-10, para_sem_imp / np.maximum(ps_max, 1e-10), 0.0)

        graph_ratio = float(self.config.paragraph_graph_importance_ratio)
        if num_nodes > 0 and (paragraph_node_matrix.nnz > 0 or paragraph_relation_matrix.nnz > 0):
            combined_para_imp = graph_ratio * para_graph_norm + (1.0 - graph_ratio) * para_sem_norm
        else:
            combined_para_imp = para_sem_norm

        # 注入时间新鲜度调制，使同一话题下近期更新的状态事实优先于早期陈旧陈述
        combined_para_imp = combined_para_imp * paragraph_freshness[:, None]
        cp_max = np.max(combined_para_imp, axis=0, keepdims=True)
        paragraph_topic_importance = np.where(
            cp_max > 1e-10,
            combined_para_imp / np.maximum(cp_max, 1e-10),
            0.0,
        ).astype(np.float32)

        summaries: List[TopicClusterSummary] = []
        for t_idx in range(topic_count):
            eff_mass = round(float(np.sum(soft_para_responsibilities[:, t_idx])), 3)
            if num_nodes > 0:
                top_node_idx = np.argsort(-node_topic_importance[:, t_idx])[:5]
                top_entities = tuple(
                    node_names[int(i)] for i in top_node_idx if float(node_topic_importance[int(i), t_idx]) > 1e-4
                )
            else:
                top_entities = ()
            top_para_idx = np.argsort(-paragraph_topic_importance[:, t_idx])[:3]
            top_paras = tuple(
                paragraph_hashes[int(i)] for i in top_para_idx if float(paragraph_topic_importance[int(i), t_idx]) > 0.0
            )
            summaries.append(
                TopicClusterSummary(
                    topic_id=t_idx,
                    effective_paragraph_mass=eff_mass,
                    top_entities=top_entities,
                    top_paragraph_hashes=top_paras,
                )
            )

        return (
            global_pr,
            node_topic_importance,
            paragraph_topic_importance,
            topic_query_centroids,
            summaries,
        )


class TopicGraphRetriever:
    """基于 FAISS 双池粗召回、位置无关消息显著性自赋权与预求解话题图扩散的零 LLM 检索器。"""

    def __init__(
        self,
        *,
        paragraph_vector_store: VectorStore,
        graph_vector_store: Optional[VectorStore] = None,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        sparse_index: Optional[SparseBM25Index] = None,
        config: Optional[TopicGraphConfig] = None,
        graph_projection_lock: Optional[ContextManager[Any]] = None,
    ):
        self.paragraph_vector_store = paragraph_vector_store
        self.graph_vector_store = paragraph_vector_store if graph_vector_store is None else graph_vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.sparse_index = sparse_index
        self.config = config or TopicGraphConfig()
        self.solver = TopicImportanceSolver(self.config)
        self._graph_projection_lock = graph_projection_lock if graph_projection_lock is not None else nullcontext()

        self._index_lock = RLock()
        self._cached_index: Optional[TopicImportanceIndex] = None

    async def _capture_graph_snapshot_async(self) -> GraphSnapshot:
        """在工作线程复制图快照，与宿主的图投影写入互斥。"""
        async with self.graph_store._lock:
            return await cancellation_safe_to_thread(self._capture_graph_snapshot_sync)

    def _capture_graph_snapshot_sync(self) -> GraphSnapshot:
        with self._graph_projection_lock:
            nodes = list(self.graph_store._nodes)
            adj_ref = self.graph_store._adjacency
            adj_copy = (
                adj_ref.copy().tocsr()
                if adj_ref is not None
                else csr_matrix((len(nodes), len(nodes)), dtype=np.float32)
            )
            return GraphSnapshot(
                node_names=nodes,
                raw_adj=adj_copy,
                graph_revision=int(self.graph_store.graph_revision),
                node_revision=int(self.graph_store.node_revision),
            )

    def _compute_store_cache_key(self, graph_rev: int, node_rev: int) -> Tuple[Any, ...]:
        """提取存储层版本指纹。"""
        stats_rows = self.metadata_store.query(
            """
            SELECT COUNT(*) AS cnt, COALESCE(MAX(updated_at), 0.0) AS max_upd
            FROM paragraphs
            WHERE (is_deleted IS NULL OR is_deleted = 0)
            """
        )
        para_cnt = int(stats_rows[0]["cnt"]) if stats_rows else 0
        max_upd = round(float(stats_rows[0]["max_upd"]), 4) if stats_rows else 0.0
        return (
            int(graph_rev),
            int(node_rev),
            int(self.paragraph_vector_store.num_vectors),
            int(self.graph_vector_store.num_vectors),
            para_cnt,
            max_upd,
        )

    def _ensure_index_with_snapshot_sync(
        self,
        snapshot: GraphSnapshot,
        *,
        force_rebuild: bool = False,
    ) -> TopicImportanceIndex:
        """在工作线程中根据快照复用缓存、执行 O(ΔN) 在线增量投影或触发全量重建。"""
        with self._index_lock:
            cache_key = self._compute_store_cache_key(snapshot.graph_revision, snapshot.node_revision)
            if not force_rebuild and self._cached_index is not None and self._cached_index.cache_key == cache_key:
                return self._cached_index

            if not force_rebuild and self._cached_index is not None:
                projected = self.solver.project_incremental(
                    existing=self._cached_index,
                    paragraph_vector_store=self.paragraph_vector_store,
                    graph_vector_store=self.graph_vector_store,
                    graph_snapshot=snapshot,
                    metadata_store=self.metadata_store,
                    cache_key=cache_key,
                )
                if projected is not None:
                    self._cached_index = projected
                    return projected

            prev_builds = self._cached_index.full_build_count if self._cached_index is not None else 0
            index = self.solver.build_index(
                paragraph_vector_store=self.paragraph_vector_store,
                graph_vector_store=self.graph_vector_store,
                graph_snapshot=snapshot,
                metadata_store=self.metadata_store,
                cache_key=cache_key,
                previous_full_builds=prev_builds,
            )
            self._cached_index = index
            return index

    def ensure_index_sync(self, *, force_rebuild: bool = False) -> TopicImportanceIndex:
        """同步获取或更新话题重要性索引。"""
        snapshot = self._capture_graph_snapshot_sync()
        return self._ensure_index_with_snapshot_sync(snapshot, force_rebuild=force_rebuild)

    async def ensure_index(self, *, force_rebuild: bool = False) -> TopicImportanceIndex:
        """异步安全地捕获锁内图快照并在工作线程中更新索引，不阻塞事件循环。"""
        snapshot = await self._capture_graph_snapshot_async()
        return await cancellation_safe_to_thread(
            self._ensure_index_with_snapshot_sync,
            snapshot,
            force_rebuild=force_rebuild,
        )

    @staticmethod
    def _clean_recent_messages(messages: Sequence[str], max_messages: int) -> List[str]:
        """过滤纯标点/空白与纯语气词刷屏消息，保留最新若干条候选文本。"""
        cleaned: List[str] = []
        for raw in messages:
            text = str(raw or "").strip()
            if not text or _PURE_NOISE_RE.match(text) or _REPEATED_LAUGH_RE.match(text):
                continue
            cleaned.append(text)
        if not cleaned:
            return []
        return cleaned[-max(1, int(max_messages)) :]

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        *,
        scope: Optional[RetrievalScope] = None,
        allowed_paragraph_ids: Optional[Collection[str]] = None,
    ) -> List[RetrievalResult]:
        """单查询入口（兼容 DualPathRetriever.retrieve 结果协议）。"""
        return await self.retrieve_from_messages(
            messages=[query],
            top_k=top_k,
            scope=scope,
            allowed_paragraph_ids=allowed_paragraph_ids,
        )

    async def retrieve_from_messages(
        self,
        messages: Sequence[str],
        top_k: int = 10,
        *,
        scope: Optional[RetrievalScope] = None,
        allowed_paragraph_ids: Optional[Collection[str]] = None,
    ) -> List[RetrievalResult]:
        """从最新若干条聊天消息执行零 LLM 显著性自举赋权 + FAISS 粗召回 + 话题图扩散检索。"""
        cleaned_messages = self._clean_recent_messages(messages, self.config.max_recent_messages)
        if not cleaned_messages or top_k <= 0:
            return []
        if scope is not None and scope.empty:
            return []

        prefix = str(self.config.query_instruction_prefix or "")
        texts_for_encode = [f"{prefix}{m}" for m in cleaned_messages] if prefix else cleaned_messages
        raw_embeddings = await self.embedding_manager.encode_batch(texts_for_encode)
        msg_matrix = np.asarray(raw_embeddings, dtype=np.float32)
        if msg_matrix.ndim == 1:
            msg_matrix = msg_matrix.reshape(1, -1)

        resolved_allowed: Optional[Set[str]] = None
        if scope is not None:
            resolved_allowed = set(scope.paragraph_ids)
        if allowed_paragraph_ids is not None:
            extra_allowed = {str(item) for item in allowed_paragraph_ids if str(item)}
            resolved_allowed = extra_allowed if resolved_allowed is None else (resolved_allowed & extra_allowed)

        snapshot = await self._capture_graph_snapshot_async()
        return await cancellation_safe_to_thread(
            self._retrieve_sync,
            snapshot=snapshot,
            cleaned_messages=cleaned_messages,
            msg_matrix=msg_matrix,
            top_k=max(1, int(top_k)),
            allowed_paragraph_ids=resolved_allowed,
        )

    @staticmethod
    def _calibrate_evt_channel(scores: np.ndarray, peak_confidence: float) -> np.ndarray:
        """结合极值统计置信度（peak_confidence ∈ [0, 1]）校准候选分，保留已去底噪通道的真实多跳衰减比例。"""
        if scores.size == 0 or peak_confidence <= 1e-6:
            return np.zeros_like(scores, dtype=np.float32)
        max_val = float(np.max(scores))
        if max_val <= 1e-8:
            return np.zeros_like(scores, dtype=np.float32)
        rel = np.clip(scores / max_val, 0.0, 1.0)
        return (rel * float(peak_confidence)).astype(np.float32, copy=False)

    def _fetch_top_paragraphs_metadata(self, target_hashes: Sequence[str]) -> Dict[str, Tuple[str, Dict[str, Any]]]:
        """仅对最终命中的 Top-K 段落按哈希批量回表读取正文与元数据。"""
        if not target_hashes:
            return {}
        placeholders = ",".join("?" for _ in target_hashes)
        rows = self.metadata_store.query(
            f"""
            SELECT hash, content, source, metadata, knowledge_type
            FROM paragraphs
            WHERE hash IN ({placeholders})
            """,
            tuple(target_hashes),
        )
        out: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for row in rows:
            p_hash = str(row["hash"]).strip()
            content = str(row["content"] or "")
            meta = coerce_metadata_dict(row.get("metadata"))
            if row.get("source"):
                meta.setdefault("source", row["source"])
            if row.get("knowledge_type"):
                meta.setdefault("knowledge_type", row["knowledge_type"])
            out[p_hash] = (content, meta)
        return out

    def _retrieve_sync(
        self,
        *,
        snapshot: GraphSnapshot,
        cleaned_messages: List[str],
        msg_matrix: np.ndarray,
        top_k: int,
        allowed_paragraph_ids: Optional[Set[str]],
    ) -> List[RetrievalResult]:
        """在工作线程中执行位置无关显著性评估、FAISS 双池粗召回、话题路由与局部图扩散。"""
        index = self._ensure_index_with_snapshot_sync(snapshot)
        num_paragraphs = len(index.paragraph_hashes)
        if num_paragraphs == 0:
            return []

        if msg_matrix.shape[1] != index.dimension:
            raise ValueError(f"消息向量维度 ({msg_matrix.shape[1]}) 与索引维度 ({index.dimension}) 不一致")

        msg_vecs, valid_msgs = _l2_normalize_rows(msg_matrix)
        if not np.any(valid_msgs):
            return []

        num_msgs = len(cleaned_messages)
        num_nodes = len(index.node_names)
        num_relations = len(index.relation_hashes)

        csls_w = float(self.config.csls_debias_weight)

        # 1. 基于段落/实体/关系三池同质探针分别估计无偏背景零分布 (μ_0, σ_0) 与 Šidák-Gaussian 极值门限
        para_mu, para_sigma = _estimate_query_null_distribution(
            msg_vecs,
            index.para_probe_vectors,
            index.fallback_para_null,
            probe_csls_bias=index.para_probe_csls_bias,
            csls_weight=csls_w,
        )
        ent_mu, ent_sigma = _estimate_query_null_distribution(
            msg_vecs,
            index.entity_probe_vectors,
            index.fallback_entity_null,
            probe_csls_bias=index.entity_probe_csls_bias,
            csls_weight=csls_w,
        )
        rel_mu, rel_sigma = _estimate_query_null_distribution(
            msg_vecs,
            index.relation_probe_vectors,
            index.fallback_relation_null,
            probe_csls_bias=index.relation_probe_csls_bias,
            csls_weight=csls_w,
        )

        # 计入窗口消息数 M 的多重检验极值分位与各粒度独立的右尾偏度补偿 λ_tail
        evt_scale = float(self.config.evt_noise_scale)
        evt_alpha = float(self.config.evt_false_alarm_alpha)
        para_z_floor = max(
            1.35,
            float(index.para_tail_factor)
            * _evt_noise_ceiling(
                max(1, num_paragraphs),
                evt_scale * 0.96,
                num_queries=num_msgs,
                alpha=evt_alpha,
                probe_count=max(8, int(index.para_probe_vectors.shape[0])),
            ),
        )
        ent_z_floor = max(
            1.45,
            float(index.entity_tail_factor)
            * _evt_noise_ceiling(
                max(1, num_nodes),
                evt_scale,
                num_queries=num_msgs,
                alpha=evt_alpha,
                probe_count=max(8, int(index.entity_probe_vectors.shape[0])),
            ),
        )
        rel_z_floor = max(
            1.45,
            float(index.relation_tail_factor)
            * _evt_noise_ceiling(
                max(1, num_relations),
                evt_scale,
                num_queries=num_msgs,
                alpha=evt_alpha,
                probe_count=max(8, int(index.relation_probe_vectors.shape[0])),
            ),
        )

        cand_p_k = max(top_k * 2, int(self.config.candidate_paragraph_top_k))
        cand_g_k = max(48, int(self.config.candidate_graph_top_k))
        sparse_enabled = bool(
            self.sparse_index is not None and self.sparse_index.config.enabled and self.config.sparse_weight > 0.0
        )

        # 2. 逐消息独立执行 FAISS 双池粗召回、特异性字面扫描与稀疏词项信噪比评估，计算位置无关显著性 ψ_m
        msg_lit_nodes: List[List[Tuple[int, float, int]]] = [[] for _ in range(num_msgs)]
        msg_vec_nodes: List[List[Tuple[int, float, float]]] = [[] for _ in range(num_msgs)]
        msg_vec_rels: List[List[Tuple[int, float, float]]] = [[] for _ in range(num_msgs)]
        msg_para_hits: List[List[Tuple[int, float, float]]] = [[] for _ in range(num_msgs)]
        msg_sparse_cache: List[Tuple[float, float, List[Dict[str, Any]]]] = [(0.0, 0.0, []) for _ in range(num_msgs)]
        msg_salience = np.zeros((num_msgs,), dtype=np.float32)

        for m_idx in range(num_msgs):
            if not valid_msgs[m_idx]:
                continue
            msg_text = cleaned_messages[m_idx]
            q_vec = msg_vecs[m_idx]
            e_mu_m = float(ent_mu[m_idx])
            e_sig_m = float(ent_sigma[m_idx])
            r_mu_m = float(rel_mu[m_idx])
            r_sig_m = float(rel_sigma[m_idx])
            p_mu_m = float(para_mu[m_idx])
            p_sig_m = float(para_sigma[m_idx])

            # 2a. 特异性实体/别名 Aho-Corasick 扫描
            best_lit_sal = 0.0
            matched_pat_count = 0
            max_pat_len = 0
            if index.pattern_to_node_indices:
                matched = index.literal_matcher.find_all(msg_text.lower())
                matched_pat_count = len(matched)
                for pat, count in matched.items():
                    pat_len = len(pat)
                    if pat_len > max_pat_len:
                        max_pat_len = pat_len
                    u_tuple = index.pattern_to_node_indices.get(pat, ())
                    for u_int in u_tuple:
                        len_factor = min(1.35, 0.62 + 0.16 * float(pat_len))
                        idf_val = float(index.node_idf[u_int])
                        lit_strength = len_factor * idf_val * (1.0 + 0.12 * log(float(count)))
                        msg_lit_nodes[m_idx].append((u_int, lit_strength, pat_len))
                        if pat_len >= 3 or matched_pat_count >= 2:
                            sal_contrib = min(1.0, (lit_strength - 0.85) / 1.85)
                        else:
                            # 孤立单个双字词降低先验显著性，防止日常口水句偶然撞上双字节点
                            sal_contrib = min(0.28, max(0.0, (lit_strength - 1.35) / 3.00))
                        if sal_contrib > best_lit_sal:
                            best_lit_sal = max(0.0, sal_contrib)

            # 2b. 图向量池 FAISS 粗召回（结合一维 CSLS 偏置消除向量 Hub 干扰）
            best_graph_z_excess = 0.0
            if self.graph_vector_store.num_vectors > 0:
                g_ids, g_scores = self.graph_vector_store.search(q_vec, k=cand_g_k)
                for g_id, raw_cos in zip(g_ids, g_scores, strict=True):
                    is_ent = False
                    item_idx: Optional[int] = None
                    if g_id.startswith("entity:"):
                        item_idx = index.node_hash_to_idx.get(g_id[7:])
                        is_ent = True
                    elif g_id.startswith("relation:"):
                        item_idx = index.rel_hash_to_idx.get(g_id[9:])
                    else:
                        u_try = index.node_hash_to_idx.get(g_id)
                        if u_try is not None:
                            item_idx = u_try
                            is_ent = True
                        else:
                            item_idx = index.rel_hash_to_idx.get(g_id)

                    if item_idx is None:
                        continue

                    if is_ent:
                        item_bias = float(index.node_csls_bias[item_idx])
                        adj_cos = float(raw_cos) - csls_w * item_bias
                        z_val = (adj_cos - e_mu_m) / e_sig_m
                        if z_val <= ent_z_floor:
                            continue
                        excess_cos = max(0.0, (adj_cos - max(0.0, e_mu_m)) / max(0.12, 1.0 - max(0.0, e_mu_m)))
                        z_excess = z_val - ent_z_floor
                        if z_excess > best_graph_z_excess:
                            best_graph_z_excess = z_excess
                        msg_vec_nodes[m_idx].append((item_idx, excess_cos, z_excess))
                    else:
                        item_bias = float(index.relation_csls_bias[item_idx])
                        adj_cos = float(raw_cos) - csls_w * item_bias
                        z_val = (adj_cos - r_mu_m) / r_sig_m
                        if z_val <= rel_z_floor:
                            continue
                        excess_cos = max(0.0, (adj_cos - max(0.0, r_mu_m)) / max(0.12, 1.0 - max(0.0, r_mu_m)))
                        z_excess = z_val - rel_z_floor
                        if z_excess > best_graph_z_excess:
                            best_graph_z_excess = z_excess
                        msg_vec_rels[m_idx].append((item_idx, excess_cos, z_excess))

            # 2c. 段落向量池 FAISS 粗召回（结合一维 CSLS 偏置补偿长对话块稀释并压制泛化短块 Hub）
            best_para_z_excess = 0.0
            if self.paragraph_vector_store.num_vectors > 0:
                p_ids, p_scores = self.paragraph_vector_store.search(
                    q_vec,
                    k=cand_p_k,
                    allowed_ids=allowed_paragraph_ids,
                )
                for p_hash, raw_cos in zip(p_ids, p_scores, strict=True):
                    p_idx = index.paragraph_hash_to_idx.get(p_hash)
                    if p_idx is None:
                        continue
                    adj_cos = float(raw_cos) - csls_w * float(index.paragraph_csls_bias[p_idx])
                    z_val = (adj_cos - p_mu_m) / p_sig_m
                    if z_val <= para_z_floor:
                        continue
                    excess_cos = max(0.0, (adj_cos - max(0.0, p_mu_m)) / max(0.12, 1.0 - max(0.0, p_mu_m)))
                    z_excess = max(0.0, z_val - para_z_floor)
                    if z_excess > best_para_z_excess:
                        best_para_z_excess = z_excess
                    msg_para_hits[m_idx].append((p_idx, excess_cos, z_val))

            # 2d. 稀疏词项信噪比探测（支撑简称/截断实体的位置无关显著性定位，并缓存结果供阶段 7 复用）
            sparse_sal = 0.0
            if sparse_enabled and self.sparse_index is not None:
                s_hits = self.sparse_index.search(
                    query=msg_text,
                    k=max(32, top_k * 3),
                    allowed_ids=allowed_paragraph_ids,
                )
                if s_hits:
                    h_scores = np.asarray([max(0.0, float(h.get("score", 0.0))) for h in s_hits], dtype=np.float32)
                    m_max_h = float(np.max(h_scores))
                    if m_max_h > 1e-6:
                        med_h = float(np.median(h_scores)) if h_scores.size >= 4 else 0.0
                        # 要求 BM25 峰值显著高于中位背景且绝对分超过口水词底噪
                        snr_gap = max(0.0, m_max_h - max(1.25, 1.45 * med_h))
                        snr_conf = min(1.0, snr_gap / 2.20)
                        msg_sparse_cache[m_idx] = (m_max_h, snr_conf, s_hits)
                        sparse_sal = 0.82 * snr_conf

            graph_sal = best_graph_z_excess / (best_graph_z_excess + 1.50) if best_graph_z_excess > 0.0 else 0.0
            para_sal = 0.80 * (best_para_z_excess / (best_para_z_excess + 1.60)) if best_para_z_excess > 0.0 else 0.0
            msg_salience[m_idx] = max(best_lit_sal, graph_sal, para_sal, sparse_sal)

        # 3. 位置无关消息权重合成：由各消息自身记忆显著性主导，时间衰减仅作平局微调（消除“末句必为正题”偏置）
        rec_tie = float(self.config.recency_tiebreak_strength)
        rec_decay = float(self.config.recency_decay)
        recency_prior = np.asarray(
            [(1.0 - rec_tie) + rec_tie * (rec_decay ** float(num_msgs - 1 - i)) for i in range(num_msgs)],
            dtype=np.float32,
        )
        recency_prior *= valid_msgs.astype(np.float32)

        max_sal = float(np.max(msg_salience))
        if max_sal > 0.08:
            rel_sal = msg_salience / max_sal
            # 低显著性闲聊消息被自适应压制至 0.06 以下，无论它排在窗口开头、中间还是末尾
            msg_weights = np.where(rel_sal >= 0.22, rel_sal * recency_prior, 0.05 * rel_sal * recency_prior).astype(
                np.float32
            )
            # 指代追问承接：仅当紧邻上一句刚刚激活了强记忆锚点且最新句为简短追问时，赋予最新句适度语义导向权重
            latest_valid = int(np.flatnonzero(valid_msgs)[-1])
            if (
                latest_valid >= 1
                and not msg_lit_nodes[latest_valid]
                and msg_salience[latest_valid] < 0.25 * max_sal
                and float(rel_sal[latest_valid - 1]) >= 0.45
                and len(cleaned_messages[latest_valid]) <= 12
            ):
                msg_weights[latest_valid] = max(float(msg_weights[latest_valid]), 0.36 * float(np.max(msg_weights)))
        else:
            # 全部消息均无显著图/段落锚点时，保留温和时间权重供后续稀疏通道或拒识门控裁决
            msg_weights = (0.30 * recency_prior).astype(np.float32)

        max_mw = float(np.max(msg_weights))
        if max_mw > 1e-8:
            msg_weights /= max_mw

        window_vec = np.sum(msg_vecs * msg_weights[:, None], axis=0, keepdims=True)
        window_vec, _ = _l2_normalize_rows(window_vec)

        # 4. 聚合全窗口图锚点种子与直接关系证据
        node_seed = np.zeros((num_nodes,), dtype=np.float32)
        direct_relation_scores = np.zeros((num_relations,), dtype=np.float32)
        peak_lit_confidence = 0.0
        peak_graph_z_excess = 0.0
        peak_para_z_excess = 0.0

        for m_idx in range(num_msgs):
            w_m = float(msg_weights[m_idx])
            if w_m <= 0.02:
                continue

            has_corroboration = bool(
                msg_vec_nodes[m_idx]
                or msg_vec_rels[m_idx]
                or msg_sparse_cache[m_idx][1] > 0.10
                or len(msg_lit_nodes[m_idx]) >= 2
            )
            for u_int, lit_strength, pat_len in msg_lit_nodes[m_idx]:
                # 孤立未佐证的双字匹配强衰减，杜绝日常口水词误唤醒图扩散
                corrob_scale = 1.0 if (pat_len >= 3 or has_corroboration) else 0.16
                score = w_m * lit_strength * corrob_scale
                if score > node_seed[u_int]:
                    node_seed[u_int] = score
                lit_conf = min(1.0, (lit_strength / 2.05) * corrob_scale) * w_m
                if lit_conf > peak_lit_confidence:
                    peak_lit_confidence = lit_conf

            for u_int, excess_cos, z_excess in msg_vec_nodes[m_idx]:
                strength = w_m * excess_cos * (z_excess / (z_excess + 1.40)) * float(index.node_idf[u_int])
                if strength > node_seed[u_int]:
                    node_seed[u_int] = strength
                if w_m * z_excess > peak_graph_z_excess:
                    peak_graph_z_excess = w_m * z_excess

            for r_int, excess_cos, z_excess in msg_vec_rels[m_idx]:
                r_strength = w_m * excess_cos * (z_excess / (z_excess + 1.30)) * float(index.relation_weights[r_int])
                if r_strength > direct_relation_scores[r_int]:
                    direct_relation_scores[r_int] = r_strength
                if w_m * z_excess > peak_graph_z_excess:
                    peak_graph_z_excess = w_m * z_excess
                u_end, v_end = index.relation_endpoints[r_int]
                if u_end >= 0:
                    ep = 0.72 * r_strength * float(index.node_idf[int(u_end)])
                    if ep > node_seed[int(u_end)]:
                        node_seed[int(u_end)] = ep
                if v_end >= 0:
                    ep = 0.72 * r_strength * float(index.node_idf[int(v_end)])
                    if ep > node_seed[int(v_end)]:
                        node_seed[int(v_end)] = ep

        # 补充窗口合成向量的关系粗召回（兼顾跨句互补指代，如 m_0 提主语、m_1 追问属性）
        if num_msgs > 1 and num_relations > 0:
            win_r_mu, win_r_sig = _estimate_query_null_distribution(
                window_vec,
                index.relation_probe_vectors,
                index.fallback_relation_null,
                probe_csls_bias=index.relation_probe_csls_bias,
                csls_weight=csls_w,
            )
            if self.graph_vector_store.num_vectors > 0:
                wg_ids, wg_scores = self.graph_vector_store.search(window_vec[0], k=min(48, cand_g_k))
                for g_id, raw_cos in zip(wg_ids, wg_scores, strict=True):
                    if not g_id.startswith("relation:"):
                        continue
                    r_idx = index.rel_hash_to_idx.get(g_id[9:])
                    if r_idx is None:
                        continue
                    adj_w_cos = float(raw_cos) - csls_w * float(index.relation_csls_bias[r_idx])
                    z_val = (adj_w_cos - float(win_r_mu[0])) / float(win_r_sig[0])
                    if z_val <= rel_z_floor:
                        continue
                    z_ex = z_val - rel_z_floor
                    ex_cos = max(
                        0.0,
                        (adj_w_cos - max(0.0, float(win_r_mu[0]))) / max(0.12, 1.0 - max(0.0, float(win_r_mu[0]))),
                    )
                    w_rel = 0.65 * ex_cos * (z_ex / (z_ex + 1.30)) * float(index.relation_weights[r_idx])
                    if w_rel > direct_relation_scores[r_idx]:
                        direct_relation_scores[r_idx] = w_rel
                    if 0.75 * z_ex > peak_graph_z_excess:
                        peak_graph_z_excess = 0.75 * z_ex

        max_seed = float(np.max(node_seed)) if num_nodes > 0 else 0.0
        node_seed_norm = (node_seed / max_seed) if max_seed > 1e-8 else node_seed

        # 5. 软话题路由（支持消融实验：当门控/调制/先验全置 0 时完全跳过话题干预）
        topic_enabled = (
            float(self.config.topic_gate_strength) > 0.0
            or float(self.config.topic_modulation_strength) > 0.0
            or float(self.config.topic_prior_boost_weight) > 0.0
        )
        topic_count = index.topic_count
        active_topic_ids: List[int] = []
        active_topic_weights = np.zeros((topic_count,), dtype=np.float32)
        topic_activation_confidence = 0.0

        if topic_enabled and topic_count > 0:
            res_msg_vecs, _ = _l2_normalize_rows(msg_vecs - index.query_cone_shift[None, :])
            raw_msg_t = res_msg_vecs @ index.topic_query_centroids.T
            if topic_count > 1:
                t_med = np.clip(np.median(raw_msg_t, axis=1, keepdims=True), 0.0, 0.88)
                msg_t_ex = np.maximum(0.0, (raw_msg_t - t_med) / np.maximum(0.12, 1.0 - t_med))
            else:
                msg_t_ex = np.maximum(0.0, raw_msg_t)
            sem_topic_score = np.max(msg_t_ex * msg_weights[:, None], axis=0)

            if num_nodes > 0 and max_seed > 1e-8:
                seed_mass = node_seed_norm / max(1e-8, float(np.sum(node_seed_norm)))
                node_topic_score = seed_mass @ index.node_topic_importance
                raw_topic_score = 0.28 * sem_topic_score + 0.72 * node_topic_score
            else:
                raw_topic_score = sem_topic_score

            sorted_t = np.sort(raw_topic_score)[::-1]
            max_t_score = float(sorted_t[0])
            margin_t = float(sorted_t[0] - sorted_t[1]) if sorted_t.size >= 2 else max_t_score
            # 话题激活置信度同时考察绝对归属分与第1/第2话题区分裕度，歧义时自动减弱门控避免误伤
            topic_activation_confidence = min(1.0, 0.55 * (max_t_score / 0.28) + 0.45 * (margin_t / 0.14))
            top_m = min(int(self.config.active_top_topics), max(1, (topic_count + 1) // 2))
            top_t_indices = np.argsort(-raw_topic_score)[:top_m]
            selected_scores = raw_topic_score[top_t_indices]
            temp = max(1e-3, float(self.config.topic_routing_temperature))
            shifted = (selected_scores - float(np.max(selected_scores))) / temp
            exp_w = np.exp(np.clip(shifted, -30.0, 0.0))
            norm_w = exp_w / max(1e-8, float(np.sum(exp_w)))

            for t_id, w_val in zip(top_t_indices, norm_w, strict=True):
                t_int = int(t_id)
                active_topic_ids.append(t_int)
                active_topic_weights[t_int] = float(w_val)

        # 6. 激活话题门控下的 1~2 跳局部图连通扩散（门控强度随话题路由置信度自适应缩放）
        if num_nodes > 0 and max_seed > 1e-8:
            gate_eta = float(self.config.topic_gate_strength) * float(topic_activation_confidence)
            if topic_enabled and topic_count > 0 and gate_eta > 0.0:
                active_node_imp = index.node_topic_importance @ active_topic_weights
                topic_node_gate = (1.0 - gate_eta) + gate_eta * active_node_imp
            else:
                topic_node_gate = np.ones((num_nodes,), dtype=np.float32)

            if index.norm_propagation.nnz > 0:
                hop1 = topic_node_gate * (index.norm_propagation @ node_seed_norm)
                hop2 = topic_node_gate * (index.norm_propagation @ hop1)
                node_connectivity = (
                    node_seed_norm + float(self.config.hop1_decay) * hop1 + float(self.config.hop2_decay) * hop2
                )
            else:
                node_connectivity = node_seed_norm * topic_node_gate
        else:
            node_connectivity = np.zeros((num_nodes,), dtype=np.float32)

        # 7. 汇总段落多通道得分（全部源自 FAISS 候选集与稀疏图传播，零全表稠密矩阵内积）
        raw_sem_scores = np.zeros((num_paragraphs,), dtype=np.float32)
        for m_idx in range(num_msgs):
            w_m = float(msg_weights[m_idx])
            if w_m <= 0.02:
                continue
            for p_idx, excess_cos, z_val in msg_para_hits[m_idx]:
                val = w_m * excess_cos
                if val > raw_sem_scores[p_idx]:
                    raw_sem_scores[p_idx] = val
                z_ex = max(0.0, z_val - para_z_floor) * w_m
                if z_ex > peak_para_z_excess:
                    peak_para_z_excess = z_ex

        raw_direct_rel_para = np.zeros((num_paragraphs,), dtype=np.float32)
        if num_relations > 0 and index.paragraph_relation_matrix.nnz > 0 and np.any(direct_relation_scores > 0.0):
            active_rel_indices = np.flatnonzero(direct_relation_scores > 0.0)
            if active_rel_indices.size > 0:
                # 仅在命中的候选段落上聚合直接关系得分
                touched_p = np.flatnonzero(
                    np.asarray(index.paragraph_relation_matrix[:, active_rel_indices].sum(axis=1)).ravel() > 0.0
                )
                for p_int in touched_p:
                    best_r = 0.0
                    extra_r = 0.0
                    for r_idx, grounding_w in index.paragraph_relation_lists[int(p_int)]:
                        s_r = float(direct_relation_scores[r_idx]) * grounding_w
                        if s_r <= 0.0:
                            continue
                        if s_r > best_r:
                            extra_r += best_r
                            best_r = s_r
                        else:
                            extra_r += s_r
                    if best_r > 0.0:
                        raw_direct_rel_para[int(p_int)] = best_r + 0.15 * extra_r

        raw_diffusion_scores = np.zeros((num_paragraphs,), dtype=np.float32)
        if num_nodes > 0 and index.paragraph_node_matrix.nnz > 0 and np.any(node_connectivity > 0.0):
            raw_diffusion_scores += np.asarray(index.paragraph_node_matrix @ node_connectivity).ravel()

        induced_rel = np.zeros((num_relations,), dtype=np.float32)
        if num_relations > 0 and num_nodes > 0 and np.any(node_connectivity > 0.0):
            u_end = index.relation_endpoints[:, 0]
            v_end = index.relation_endpoints[:, 1]
            u_conn = np.where(u_end >= 0, node_connectivity[np.maximum(u_end, 0)], 0.0)
            v_conn = np.where(v_end >= 0, node_connectivity[np.maximum(v_end, 0)], 0.0)
            co_active_bonus = np.where((u_conn > 1e-4) & (v_conn > 1e-4), 1.15, 0.45)
            induced_rel = 0.5 * (u_conn + v_conn) * co_active_bonus * index.relation_weights
            if index.paragraph_relation_matrix.nnz > 0:
                raw_diffusion_scores += 0.45 * np.asarray(index.paragraph_relation_matrix @ induced_rel).ravel()

        max_dir_rel = float(np.max(direct_relation_scores)) if num_relations > 0 else 0.0
        norm_dir_rel = direct_relation_scores / max_dir_rel if max_dir_rel > 1e-8 else direct_relation_scores
        max_ind_rel = float(np.max(induced_rel)) if num_relations > 0 else 0.0
        norm_ind_rel = induced_rel / max_ind_rel if max_ind_rel > 1e-8 else induced_rel
        relation_reach_scores = np.maximum(norm_dir_rel, 0.65 * norm_ind_rel)

        # 稀疏 BM25 通道：直接复用阶段 2d 已计算并经过信噪比门控的稀疏命中结果
        raw_sparse_scores = np.zeros((num_paragraphs,), dtype=np.float32)
        peak_sparse_confidence = 0.0
        if sparse_enabled:
            for m_int in range(num_msgs):
                w_m = float(msg_weights[m_int])
                if w_m < 0.22:
                    continue
                msg_max_hit, snr_conf, sparse_hits = msg_sparse_cache[m_int]
                if snr_conf <= 1e-6 or msg_max_hit <= 1e-6 or not sparse_hits:
                    continue
                if w_m * snr_conf > peak_sparse_confidence:
                    peak_sparse_confidence = w_m * snr_conf
                for hit in sparse_hits:
                    p_idx = index.paragraph_hash_to_idx.get(str(hit.get("hash", "")))
                    if p_idx is None:
                        continue
                    hit_val = max(0.0, float(hit.get("score", 0.0)))
                    norm_hit = (hit_val / msg_max_hit) * snr_conf
                    weighted_hit = w_m * norm_hit
                    if weighted_hit > raw_sparse_scores[p_idx]:
                        raw_sparse_scores[p_idx] = weighted_hit + 0.15 * raw_sparse_scores[p_idx]
                    else:
                        raw_sparse_scores[p_idx] += 0.15 * weighted_hit

        if topic_enabled and topic_count > 0:
            para_topic_scores = (index.paragraph_topic_importance @ active_topic_weights).astype(np.float32)
        else:
            para_topic_scores = np.zeros((num_paragraphs,), dtype=np.float32)

        if allowed_paragraph_ids is not None:
            allowed_mask = np.asarray(
                [h in allowed_paragraph_ids for h in index.paragraph_hashes],
                dtype=bool,
            )
            if not np.any(allowed_mask):
                return []
            raw_sem_scores = np.where(allowed_mask, raw_sem_scores, 0.0)
            raw_direct_rel_para = np.where(allowed_mask, raw_direct_rel_para, 0.0)
            raw_diffusion_scores = np.where(allowed_mask, raw_diffusion_scores, 0.0)
            raw_sparse_scores = np.where(allowed_mask, raw_sparse_scores, 0.0)
            para_topic_scores = np.where(allowed_mask, para_topic_scores, 0.0)

        # 8. 基于极值统计信噪比的绝对置信度校准与纯闲聊拒识
        sem_peak_conf = peak_para_z_excess / (peak_para_z_excess + 1.65) if peak_para_z_excess > 0.0 else 0.0
        rel_peak_conf = peak_graph_z_excess / (peak_graph_z_excess + 1.75) if peak_graph_z_excess > 0.0 else 0.0
        diff_peak_conf = max(peak_lit_confidence, rel_peak_conf)

        cal_sem = self._calibrate_evt_channel(raw_sem_scores, sem_peak_conf)
        cal_rel = self._calibrate_evt_channel(raw_direct_rel_para, rel_peak_conf)
        cal_diff = self._calibrate_evt_channel(raw_diffusion_scores, diff_peak_conf)
        if np.any(cal_rel > 0.0):
            cal_conn = (0.55 * cal_rel + 0.45 * cal_diff).astype(np.float32)
            raw_conn_scores = raw_direct_rel_para + 0.55 * raw_diffusion_scores
        else:
            cal_conn = cal_diff
            raw_conn_scores = raw_diffusion_scores
        cal_sparse = self._calibrate_evt_channel(raw_sparse_scores, peak_sparse_confidence)

        w_sem = float(self.config.semantic_weight)
        w_conn = float(self.config.graph_connectivity_weight)
        w_sparse = float(self.config.sparse_weight) * float(peak_sparse_confidence)
        weight_sum = max(1e-6, w_sem + w_conn + w_sparse)

        base_score = (w_sem * cal_sem + w_conn * cal_conn + w_sparse * cal_sparse) / weight_sum
        # 融入平滑时间新鲜度微调，使同等证据强度下最新修订的记忆排在前列
        fresh_bonus = 0.94 + 0.06 * index.paragraph_freshness
        base_score = base_score * fresh_bonus

        # 整窗触发门控：先在无话题先验抬升前检验基础证据峰值，防止话题先验把纯闲聊噪声救活为假阳性
        trigger_gate = float(self.config.query_trigger_threshold)
        if float(np.max(base_score)) < trigger_gate:
            return []

        if topic_enabled:
            mod_gamma = float(self.config.topic_modulation_strength) * float(topic_activation_confidence)
            topic_modulator = (1.0 - mod_gamma) + mod_gamma * para_topic_scores
            evidence_gate = ((cal_conn > 0.05) | (cal_sem > 0.14) | (cal_sparse > 0.18)).astype(np.float32)
            prior_boost = (
                float(self.config.topic_prior_boost_weight)
                * float(topic_activation_confidence)
                * para_topic_scores
                * evidence_gate
            )
            final_scores = base_score * topic_modulator + prior_boost
        else:
            final_scores = base_score

        min_rel_gate = float(self.config.min_relevance_score)
        if float(np.max(final_scores)) < min_rel_gate:
            return []

        ranked_indices = np.argsort(-final_scores)
        winner_p_indices: List[int] = []
        for idx_val in ranked_indices:
            p_idx = int(idx_val)
            if float(final_scores[p_idx]) < min_rel_gate:
                break
            winner_p_indices.append(p_idx)
            if len(winner_p_indices) >= top_k:
                break

        if not winner_p_indices:
            return []

        # 仅回表查询最终 Top-K 段落的正文与元数据
        winner_hashes = [index.paragraph_hashes[i] for i in winner_p_indices]
        fetched_meta_map = self._fetch_top_paragraphs_metadata(winner_hashes)

        topic_routing_meta = [
            {
                "topic_id": t_id,
                "weight": round(float(active_topic_weights[t_id]), 4),
                "top_entities": list(index.topic_summaries[t_id].top_entities[:4])
                if t_id < len(index.topic_summaries)
                else [],
            }
            for t_id in active_topic_ids
        ]

        results: List[RetrievalResult] = []
        for p_idx in winner_p_indices:
            p_hash = index.paragraph_hashes[p_idx]
            content, base_meta = fetched_meta_map.get(p_hash, ("", {}))
            score_val = float(final_scores[p_idx])

            # 仅输出真正达到有效激活阈值的精准支撑关系，拒绝无差别打包灌水
            evidence_items: List[Dict[str, Any]] = []
            for r_idx, grounding_w in index.paragraph_relation_lists[p_idx]:
                rel_score = float(relation_reach_scores[r_idx]) * grounding_w
                if rel_score >= 0.22:
                    evidence_items.append(
                        {
                            "type": "relation",
                            "hash": index.relation_hashes[r_idx],
                            "score": round(rel_score, 6),
                        }
                    )
            evidence_items.sort(key=lambda item: float(item["score"]), reverse=True)

            meta = dict(base_meta)
            meta["evidence_items"] = evidence_items[:4]
            meta["topic_routing"] = topic_routing_meta
            meta["score_breakdown"] = {
                "final": round(score_val, 6),
                "semantic": round(float(cal_sem[p_idx]), 6),
                "semantic_raw": round(float(raw_sem_scores[p_idx]), 6),
                "graph_evidence": round(float(cal_conn[p_idx]), 6),
                "graph_connectivity_raw": round(float(raw_conn_scores[p_idx]), 6),
                "sparse": round(float(cal_sparse[p_idx]), 6),
                "topic_importance": round(float(para_topic_scores[p_idx]), 6),
                "freshness": round(float(index.paragraph_freshness[p_idx]), 6),
                "topic_activation_confidence": round(float(topic_activation_confidence), 6),
            }

            source_tag = "topic_graph_fusion" if float(cal_conn[p_idx]) > 0.0 else "topic_semantic"
            results.append(
                RetrievalResult(
                    hash_value=p_hash,
                    content=content,
                    score=score_val,
                    result_type="paragraph",
                    source=source_tag,
                    metadata=meta,
                )
            )

        return results


__all__ = [
    "GraphSnapshot",
    "TopicClusterSummary",
    "TopicGraphConfig",
    "TopicGraphRetriever",
    "TopicImportanceIndex",
    "TopicImportanceSolver",
]

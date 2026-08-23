"""评估表达向量索引的动态聚类数量与路由池策略。

该脚本使用历史表达选择 query embedding 和当前表达向量，以全库精确余弦
Top-K 作为聚类路由真值，比较不同样本量、簇数量和预筛池目标下的召回率。
它只读取现有索引并输出评估报告，不修改线上向量索引。
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import argparse
import hashlib
import json
import math
import time

import faiss
import numpy as np


ROOT_PATH = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_JSON = "data/expression_selection/expression_vector_index.json"
DEFAULT_QUERY_NPZ = (
    "data/analysis/expression_selection_eval/indices/cache/expression_selection_query_embeddings.npz"
)
DEFAULT_LLM_SELECTION_BATCH = (
    "data/analysis/expression_selection_eval/runs/selector_batches/"
    "expression_selection_batch_compare_size4000_clean_intentprompt_fixedbaseline_20260621.json"
)
DEFAULT_OUTPUT_JSON = "data/analysis/expression_cluster_count_benchmark.json"
DEFAULT_OUTPUT_MD = "data/analysis/expression_cluster_count_benchmark.md"
DEFAULT_SAMPLE_SIZES = "500,1000,2000,4000,8000,12000,0"
DEFAULT_CLUSTER_COUNTS = "16,24,32,48,64,80,96,128,160,192,256,320,384,512,640"
DEFAULT_POOL_TARGETS = "0,1000,1500,2000,2500,3000"


@dataclass(frozen=True)
class BenchmarkRow:
    """一组聚类与路由参数的评估结果。"""

    sample_count: int
    cluster_count: int
    cluster_pool_minimum: int
    raw_pool_target: int
    raw_pool_mean: float
    raw_pool_p95: float
    scan_ratio_mean: float
    recall_at_10_mean: float
    recall_at_10_p10: float
    recall_at_50_mean: float
    recall_at_50_p10: float
    mean_top50_similarity_loss: float
    kmeans_seconds: float
    route_seconds: float
    cluster_size_mean: float
    cluster_size_p95: float
    cluster_size_max: int
    llm_selected_target_count: int
    llm_selected_coverage: Optional[float]


@dataclass(frozen=True)
class ExactPrefilterBenchmark:
    """全库精确向量预筛的质量与耗时。"""

    sample_count: int
    raw_pool_size: int
    recall_at_50: float
    llm_selected_target_count: int
    llm_selected_coverage: Optional[float]
    latency_median_ms: float
    latency_p95_ms: float


def build_argument_parser() -> ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="评估表达向量索引的动态聚类数量。")
    parser.add_argument("--index-json", default=DEFAULT_INDEX_JSON, help="表达向量索引 JSON。")
    parser.add_argument("--query-npz", default=DEFAULT_QUERY_NPZ, help="历史 query embedding NPZ。")
    parser.add_argument(
        "--llm-selection-batch",
        default=DEFAULT_LLM_SELECTION_BATCH,
        help="包含旧 LLM 最终选中表达的 selector batch；空字符串表示不评估。",
    )
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON, help="JSON 报告输出路径。")
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD, help="Markdown 摘要输出路径。")
    parser.add_argument("--sample-sizes", default=DEFAULT_SAMPLE_SIZES, help="逗号分隔的样本量；0 表示全量。")
    parser.add_argument("--cluster-counts", default=DEFAULT_CLUSTER_COUNTS, help="逗号分隔的候选簇数。")
    parser.add_argument("--pool-targets", default=DEFAULT_POOL_TARGETS, help="逗号分隔的原始预筛池目标；0 表示仅取 16 簇。")
    parser.add_argument("--top-k", type=int, default=50, help="精确召回真值的 Top-K。")
    parser.add_argument("--cluster-pool-minimum", type=int, default=16, help="至少扫描多少个近邻簇。")
    parser.add_argument("--kmeans-iterations", type=int, default=20, help="Faiss spherical k-means 迭代次数。")
    parser.add_argument("--seed", type=int, default=42, help="固定随机种子。")
    return parser


def parse_args() -> Namespace:
    """解析命令行参数。"""

    return build_argument_parser().parse_args()


def resolve_path(raw_path: str) -> Path:
    """将相对路径按项目根目录解析。"""

    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else ROOT_PATH / path


def parse_integer_list(raw_value: str, *, allow_zero: bool) -> List[int]:
    """解析去重、升序的逗号分隔整数。"""

    values = sorted({int(part.strip()) for part in raw_value.split(",") if part.strip()})
    minimum = 0 if allow_zero else 1
    if not values or any(value < minimum for value in values):
        raise ValueError(f"整数列表包含非法值: {raw_value!r}")
    return values


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """复制并 L2 归一化二维 float32 矩阵。"""

    normalized = np.ascontiguousarray(matrix.astype(np.float32, copy=True))
    if normalized.ndim != 2 or normalized.shape[0] == 0:
        raise ValueError(f"向量矩阵形状非法: {normalized.shape}")
    faiss.normalize_L2(normalized)
    return normalized


def load_vectors(index_path: Path) -> Tuple[dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    """读取当前 profile 的表达向量、聚类中心与标签。"""

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    profiles = payload.get("embedding_profiles") or []
    if not profiles:
        raise ValueError("表达向量索引缺少 embedding_profiles")
    profile = max(profiles, key=lambda item: int(item.get("expression_count") or 0))
    vectors_path = index_path.with_name(str(payload["vectors_file"]))
    with np.load(vectors_path, allow_pickle=False) as vector_file:
        vectors = normalize_rows(vector_file[str(profile["vectors_key"])])
        centers = normalize_rows(vector_file[str(profile["cluster_centers_key"])])

    marker = str(profile["marker"])
    labels = np.full(vectors.shape[0], -1, dtype=np.int32)
    for expression in payload.get("expressions") or []:
        if str(expression.get("embedding_profile_marker") or "") != marker:
            continue
        labels[int(expression["vector_index"])] = int(expression.get("cluster_id") or 0)
    if np.any(labels < 0):
        raise ValueError(f"当前 profile 有 {int(np.count_nonzero(labels < 0))} 条向量缺少聚类标签")
    return payload, vectors, centers, labels


def load_queries(query_path: Path, expected_dimension: int) -> Tuple[np.ndarray, List[str], str]:
    """读取历史表达选择 query embedding。"""

    with np.load(query_path, allow_pickle=False) as query_file:
        queries = normalize_rows(query_file["embeddings"])
        hashes = [str(value) for value in query_file["hashes"].tolist()]
        model_name = str(query_file["model_name"].item())
    if queries.shape[1] != expected_dimension:
        raise ValueError(
            f"query embedding 维度不一致: query={queries.shape[1]}, expression={expected_dimension}"
        )
    if len(hashes) != queries.shape[0]:
        raise ValueError(f"query hash 数量不一致: hashes={len(hashes)}, queries={queries.shape[0]}")
    return queries, hashes, model_name


def load_llm_selected_targets(
    batch_path: Path,
    *,
    query_hashes: Sequence[str],
    index_payload: dict[str, Any],
) -> Dict[int, List[int]]:
    """把旧 selector batch 中 LLM 最终选中的表达映射到当前向量下标。"""

    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    current_marker = str(index_payload.get("embedding_profile_marker") or "")
    vector_index_by_expression_id = {
        int(expression["id"]): int(expression["vector_index"])
        for expression in index_payload.get("expressions") or []
        if str(expression.get("embedding_profile_marker") or "") == current_marker
    }
    query_index_by_hash = {query_hash: index for index, query_hash in enumerate(query_hashes)}
    targets: Dict[int, List[int]] = {}
    for sample in batch.get("samples") or []:
        query_text = str(sample.get("query_text") or "")
        candidate_hashes = [
            hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
            hashlib.sha256(query_text.strip().encode("utf-8")).hexdigest(),
        ]
        query_index = next(
            (query_index_by_hash[value] for value in candidate_hashes if value in query_index_by_hash),
            None,
        )
        if query_index is None:
            continue
        selected_ids = (sample.get("vector_recall") or {}).get("selected_ids") or []
        vector_indices = [
            vector_index_by_expression_id[int(expression_id)]
            for expression_id in selected_ids
            if int(expression_id) in vector_index_by_expression_id
        ]
        if vector_indices:
            targets[query_index] = vector_indices
    return targets


def choose_nested_sample_indices(total_count: int, seed: int) -> np.ndarray:
    """生成可复现的嵌套样本顺序，使不同样本量之间可直接比较。"""

    rng = np.random.default_rng(seed)
    return rng.permutation(total_count)


def build_cluster_counts(sample_count: int, requested_counts: Sequence[int]) -> List[int]:
    """合并固定网格和待评估的动态公式结果。"""

    formula_counts = {
        min(80, sample_count),
        min(80, max(2, int(round(math.sqrt(sample_count * 2.0))))),
        min(sample_count, max(2, int(round(math.sqrt(sample_count * 2.0))))),
        min(sample_count, max(2, int(math.ceil(sample_count / 64.0)))),
        min(sample_count, max(2, int(math.ceil(sample_count / 48.0)))),
        min(sample_count, max(2, int(math.ceil(sample_count / 32.0)))),
        recommended_cluster_count(sample_count),
    }
    formula_counts.update(min(sample_count, count) for count in requested_counts)
    return sorted(count for count in formula_counts if count >= 2)


def train_clusters(
    vectors: np.ndarray,
    *,
    cluster_count: int,
    iterations: int,
    seed: int,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """训练 spherical k-means，并返回中心、标签与耗时。"""

    started_at = time.perf_counter()
    kmeans = faiss.Kmeans(
        vectors.shape[1],
        cluster_count,
        niter=iterations,
        nredo=1,
        seed=seed,
        spherical=True,
        verbose=False,
        gpu=False,
        min_points_per_centroid=1,
        max_points_per_centroid=max(256, vectors.shape[0]),
    )
    kmeans.train(vectors)
    centers = normalize_rows(kmeans.centroids)
    _, raw_labels = kmeans.index.search(vectors, 1)
    return centers, raw_labels[:, 0].astype(np.int32), time.perf_counter() - started_at


def exact_top_indices(similarities: np.ndarray, top_k: int) -> np.ndarray:
    """返回每条 query 的精确 Top-K 下标。"""

    effective_top_k = min(top_k, similarities.shape[1])
    partition_start = similarities.shape[1] - effective_top_k
    unordered = np.argpartition(similarities, partition_start, axis=1)[:, partition_start:]
    unordered_scores = np.take_along_axis(similarities, unordered, axis=1)
    order = np.argsort(unordered_scores, axis=1)[:, ::-1]
    return np.take_along_axis(unordered, order, axis=1)


def select_cluster_ids(
    ordered_cluster_ids: np.ndarray,
    cluster_sizes: np.ndarray,
    *,
    minimum_clusters: int,
    raw_pool_target: int,
) -> np.ndarray:
    """按中心相似度依次选簇，直到同时满足簇数和原始池目标。"""

    selected: List[int] = []
    pooled_count = 0
    effective_minimum = min(minimum_clusters, ordered_cluster_ids.shape[0])
    for cluster_id in ordered_cluster_ids.tolist():
        selected.append(int(cluster_id))
        pooled_count += int(cluster_sizes[cluster_id])
        if len(selected) >= effective_minimum and pooled_count >= raw_pool_target:
            break
    return np.array(selected, dtype=np.int32)


def evaluate_route(
    vectors: np.ndarray,
    queries: np.ndarray,
    centers: np.ndarray,
    labels: np.ndarray,
    exact_similarities: np.ndarray,
    exact_top50: np.ndarray,
    *,
    cluster_pool_minimum: int,
    raw_pool_target: int,
    kmeans_seconds: float,
    llm_selected_targets: Optional[Dict[int, List[int]]] = None,
) -> BenchmarkRow:
    """评估一组聚类结果的路由召回、扫描池规模与相似度损失。"""

    started_at = time.perf_counter()
    cluster_sizes = np.bincount(labels, minlength=centers.shape[0])
    ordered_clusters = np.argsort(queries @ centers.T, axis=1)[:, ::-1]
    pool_sizes: List[int] = []
    recalls_at_10: List[float] = []
    recalls_at_50: List[float] = []
    similarity_losses: List[float] = []
    llm_selected_hits: List[bool] = []

    for query_index in range(queries.shape[0]):
        selected_cluster_ids = select_cluster_ids(
            ordered_clusters[query_index],
            cluster_sizes,
            minimum_clusters=cluster_pool_minimum,
            raw_pool_target=raw_pool_target,
        )
        pool_mask = np.isin(labels, selected_cluster_ids)
        pool_indices = np.flatnonzero(pool_mask)
        pool_sizes.append(int(pool_indices.shape[0]))

        gold50 = exact_top50[query_index]
        gold10 = gold50[: min(10, gold50.shape[0])]
        recalls_at_10.append(float(np.mean(pool_mask[gold10])))
        recalls_at_50.append(float(np.mean(pool_mask[gold50])))

        effective_top_k = min(gold50.shape[0], pool_indices.shape[0])
        pool_scores = exact_similarities[query_index, pool_indices]
        pool_top_scores = np.partition(pool_scores, pool_scores.shape[0] - effective_top_k)[-effective_top_k:]
        gold_top_scores = exact_similarities[query_index, gold50[:effective_top_k]]
        similarity_losses.append(float(np.mean(gold_top_scores) - np.mean(pool_top_scores)))
        if llm_selected_targets and query_index in llm_selected_targets:
            llm_selected_hits.extend(
                bool(pool_mask[vector_index]) for vector_index in llm_selected_targets[query_index]
            )

    route_seconds = time.perf_counter() - started_at
    return BenchmarkRow(
        sample_count=vectors.shape[0],
        cluster_count=centers.shape[0],
        cluster_pool_minimum=min(cluster_pool_minimum, centers.shape[0]),
        raw_pool_target=raw_pool_target,
        raw_pool_mean=round(float(np.mean(pool_sizes)), 3),
        raw_pool_p95=round(float(np.percentile(pool_sizes, 95)), 3),
        scan_ratio_mean=round(float(np.mean(pool_sizes) / vectors.shape[0]), 6),
        recall_at_10_mean=round(float(np.mean(recalls_at_10)), 6),
        recall_at_10_p10=round(float(np.percentile(recalls_at_10, 10)), 6),
        recall_at_50_mean=round(float(np.mean(recalls_at_50)), 6),
        recall_at_50_p10=round(float(np.percentile(recalls_at_50, 10)), 6),
        mean_top50_similarity_loss=round(float(np.mean(similarity_losses)), 8),
        kmeans_seconds=round(kmeans_seconds, 6),
        route_seconds=round(route_seconds, 6),
        cluster_size_mean=round(float(np.mean(cluster_sizes)), 3),
        cluster_size_p95=round(float(np.percentile(cluster_sizes, 95)), 3),
        cluster_size_max=int(np.max(cluster_sizes)),
        llm_selected_target_count=len(llm_selected_hits),
        llm_selected_coverage=(round(float(np.mean(llm_selected_hits)), 6) if llm_selected_hits else None),
    )


def evaluate_current_index(
    vectors: np.ndarray,
    queries: np.ndarray,
    centers: np.ndarray,
    labels: np.ndarray,
    *,
    top_k: int,
    cluster_pool_minimum: int,
    llm_selected_targets: Optional[Dict[int, List[int]]] = None,
) -> BenchmarkRow:
    """评估磁盘中的当前线上索引，作为真实基线。"""

    similarities = queries @ vectors.T
    exact_top50 = exact_top_indices(similarities, top_k)
    return evaluate_route(
        vectors,
        queries,
        centers,
        labels,
        similarities,
        exact_top50,
        cluster_pool_minimum=cluster_pool_minimum,
        raw_pool_target=0,
        kmeans_seconds=0.0,
        llm_selected_targets=llm_selected_targets,
    )


def benchmark_exact_prefilter(
    vectors: np.ndarray,
    queries: np.ndarray,
    *,
    raw_pool_size: int,
    llm_selected_targets: Optional[Dict[int, List[int]]] = None,
) -> ExactPrefilterBenchmark:
    """评估全库矩阵乘法后直接取 Top-N 的精确预筛方案。"""

    effective_pool_size = min(max(1, raw_pool_size), vectors.shape[0])
    similarities = queries @ vectors.T
    exact_pool_indices = exact_top_indices(similarities, effective_pool_size)
    llm_selected_hits: List[bool] = []
    if llm_selected_targets:
        for query_index, vector_indices in llm_selected_targets.items():
            pool_index_set = set(int(value) for value in exact_pool_indices[query_index].tolist())
            llm_selected_hits.extend(vector_index in pool_index_set for vector_index in vector_indices)

    timing_query_count = min(100, queries.shape[0])
    for query_index in range(min(5, timing_query_count)):
        scores = vectors @ queries[query_index]
        np.argpartition(scores, scores.shape[0] - effective_pool_size)[-effective_pool_size:]
    latencies_ms: List[float] = []
    for query_index in range(timing_query_count):
        started_at = time.perf_counter()
        scores = vectors @ queries[query_index]
        np.argpartition(scores, scores.shape[0] - effective_pool_size)[-effective_pool_size:]
        latencies_ms.append((time.perf_counter() - started_at) * 1000.0)

    return ExactPrefilterBenchmark(
        sample_count=vectors.shape[0],
        raw_pool_size=effective_pool_size,
        recall_at_50=1.0,
        llm_selected_target_count=len(llm_selected_hits),
        llm_selected_coverage=(round(float(np.mean(llm_selected_hits)), 6) if llm_selected_hits else None),
        latency_median_ms=round(float(np.median(latencies_ms)), 6),
        latency_p95_ms=round(float(np.percentile(latencies_ms, 95)), 6),
    )


def formula_cluster_counts(sample_count: int) -> Dict[str, int]:
    """给出报告中重点比较的动态簇数公式。"""

    return {
        "fixed_80": min(80, sample_count),
        "current_auto_capped_80": min(80, max(2, int(round(math.sqrt(sample_count * 2.0))))),
        "sqrt_2n_uncapped": min(sample_count, max(2, int(round(math.sqrt(sample_count * 2.0))))),
        "target_64_members": min(sample_count, max(2, int(math.ceil(sample_count / 64.0)))),
        "target_48_members": min(sample_count, max(2, int(math.ceil(sample_count / 48.0)))),
        "target_32_members": min(sample_count, max(2, int(math.ceil(sample_count / 32.0)))),
        "target_28_members_capped_640": recommended_cluster_count(sample_count),
    }


def recommended_cluster_count(sample_count: int) -> int:
    """根据实测结果计算推荐簇数：每簇约 28 条，并限制维护成本。"""

    return min(sample_count, 640, max(2, 32, int(math.ceil(sample_count / 28.0))))


def recommended_raw_pool_target(sample_count: int) -> int:
    """根据库规模缓慢扩大聚类后的原始预筛池。"""

    if sample_count <= 1:
        return sample_count
    logarithmic_steps = max(1, int(math.floor(math.log2(sample_count / 500.0))))
    return min(2500, max(1, sample_count // 2), 500 * logarithmic_steps)


def find_row(
    rows: Sequence[BenchmarkRow],
    *,
    sample_count: int,
    cluster_count: int,
    raw_pool_target: int,
) -> BenchmarkRow:
    """查找唯一的指定实验行。"""

    matches = [
        row
        for row in rows
        if row.sample_count == sample_count
        and row.cluster_count == cluster_count
        and row.raw_pool_target == raw_pool_target
    ]
    if len(matches) != 1:
        raise ValueError(
            f"实验行不唯一: samples={sample_count}, clusters={cluster_count}, pool={raw_pool_target}"
        )
    return matches[0]


def choose_recommendations(rows: Sequence[BenchmarkRow], sample_sizes: Sequence[int]) -> List[dict[str, Any]]:
    """在召回约束下，为每个样本量选择扫描池最小的参数。"""

    recommendations: List[dict[str, Any]] = []
    for sample_count in sample_sizes:
        sample_rows = [
            row
            for row in rows
            if row.sample_count == sample_count
            and row.recall_at_50_mean >= 0.90
            and row.recall_at_50_p10 >= 0.75
        ]
        if not sample_rows:
            continue
        best = min(
            sample_rows,
            key=lambda row: (row.raw_pool_mean, -row.recall_at_50_mean, row.cluster_count),
        )
        recommendations.append(asdict(best))
    return recommendations


def render_markdown(
    *,
    metadata: dict[str, Any],
    current_baseline: BenchmarkRow,
    exact_prefilter: ExactPrefilterBenchmark,
    formula_rows: Sequence[dict[str, Any]],
    recommended_rows: Sequence[dict[str, Any]],
    recommendations: Sequence[dict[str, Any]],
) -> str:
    """生成便于人工查看的 Markdown 摘要。"""

    lines = [
        "# 表达方式动态聚类数量评估",
        "",
        f"- 生成时间：{metadata['generated_at']}",
        f"- 当前表达向量：{metadata['expression_count']} 条",
        f"- 历史真实 query embedding：{metadata['query_count']} 条",
        f"- embedding 模型：{metadata['query_embedding_model']}",
        "- 真值：每条 query 对当前候选库做精确余弦 Top-50",
        "",
        "## 当前线上索引基线",
        "",
        "| 簇数 | 近邻簇数 | 平均预筛池 | Top-10 召回 | Top-50 召回 | Top-50 P10 | 旧 LLM 选中覆盖 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {current_baseline.cluster_count} | {current_baseline.cluster_pool_minimum} | "
            f"{current_baseline.raw_pool_mean:.0f} | {current_baseline.recall_at_10_mean:.3f} | "
            f"{current_baseline.recall_at_50_mean:.3f} | {current_baseline.recall_at_50_p10:.3f} | "
            f"{current_baseline.llm_selected_coverage:.3f} |"
        ),
        "",
        "## 全库精确向量预筛",
        "",
        "| 表达数 | 预筛池 | Top-50 召回 | 旧 LLM 选中覆盖 | 中位耗时 | P95 耗时 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {exact_prefilter.sample_count} | {exact_prefilter.raw_pool_size} | "
            f"{exact_prefilter.recall_at_50:.3f} | {exact_prefilter.llm_selected_coverage:.3f} | "
            f"{exact_prefilter.latency_median_ms:.3f} ms | {exact_prefilter.latency_p95_ms:.3f} ms |"
        ),
        "",
        "## 动态公式对照",
        "",
        "下表均至少取 16 个近邻簇；`预筛目标=0` 表示保持当前固定 16 簇行为。",
        "",
        "| 表达数 | 公式 | 簇数 | 预筛目标 | 平均预筛池 | Top-50 召回 | Top-50 P10 | 旧 LLM 选中覆盖 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in formula_rows:
        llm_coverage = item["llm_selected_coverage"]
        llm_coverage_text = f"{llm_coverage:.3f}" if llm_coverage is not None else "-"
        lines.append(
            f"| {item['sample_count']} | {item['formula']} | {item['cluster_count']} | "
            f"{item['raw_pool_target']} | {item['raw_pool_mean']:.0f} | "
            f"{item['recall_at_50_mean']:.3f} | {item['recall_at_50_p10']:.3f} | "
            f"{llm_coverage_text} |"
        )

    lines.extend(
        [
            "",
            "## 推荐动态标准",
            "",
            "簇数：`min(640, max(32, ceil(N / 28)))`。",
            "预筛目标：`min(2500, N / 2, 500 × max(1, floor(log2(N / 500))))`。",
            "达到预筛目标前按中心相似度继续取簇，不再固定只取 16 簇。",
            "",
            "| 表达数 | 簇数 | 预筛目标 | 平均预筛池 | Top-50 召回 | Top-50 P10 | 旧 LLM 选中覆盖 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in recommended_rows:
        llm_coverage = item["llm_selected_coverage"]
        llm_coverage_text = f"{llm_coverage:.3f}" if llm_coverage is not None else "-"
        lines.append(
            f"| {item['sample_count']} | {item['cluster_count']} | {item['raw_pool_target']} | "
            f"{item['raw_pool_mean']:.0f} | {item['recall_at_50_mean']:.3f} | "
            f"{item['recall_at_50_p10']:.3f} | {llm_coverage_text} |"
        )

    lines.extend(
        [
            "",
            "## 约束下的最小扫描池",
            "",
            "约束：平均 Top-50 召回不低于 0.90，且较差的 10% query 召回不低于 0.75。",
            "",
            "| 表达数 | 簇数 | 预筛目标 | 平均预筛池 | 扫描比例 | Top-50 召回 | Top-50 P10 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in recommendations:
        lines.append(
            f"| {item['sample_count']} | {item['cluster_count']} | {item['raw_pool_target']} | "
            f"{item['raw_pool_mean']:.0f} | {item['scan_ratio_mean']:.3f} | "
            f"{item['recall_at_50_mean']:.3f} | {item['recall_at_50_p10']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """运行完整评估并写入 JSON 与 Markdown 报告。"""

    args = parse_args()
    index_path = resolve_path(args.index_json)
    query_path = resolve_path(args.query_npz)
    output_json_path = resolve_path(args.output_json)
    output_md_path = resolve_path(args.output_md)
    requested_sample_sizes = parse_integer_list(args.sample_sizes, allow_zero=True)
    requested_cluster_counts = parse_integer_list(args.cluster_counts, allow_zero=False)
    pool_targets = parse_integer_list(args.pool_targets, allow_zero=True)

    index_payload, all_vectors, current_centers, current_labels = load_vectors(index_path)
    queries, query_hashes, query_model_name = load_queries(query_path, all_vectors.shape[1])
    llm_selected_targets = (
        load_llm_selected_targets(
            resolve_path(args.llm_selection_batch),
            query_hashes=query_hashes,
            index_payload=index_payload,
        )
        if str(args.llm_selection_batch).strip()
        else {}
    )
    sample_sizes = sorted(
        {
            all_vectors.shape[0] if size == 0 else min(size, all_vectors.shape[0])
            for size in requested_sample_sizes
        }
    )
    sample_order = choose_nested_sample_indices(all_vectors.shape[0], args.seed)
    current_baseline = evaluate_current_index(
        all_vectors,
        queries,
        current_centers,
        current_labels,
        top_k=args.top_k,
        cluster_pool_minimum=args.cluster_pool_minimum,
        llm_selected_targets=llm_selected_targets,
    )
    exact_prefilter = benchmark_exact_prefilter(
        all_vectors,
        queries,
        raw_pool_size=2500,
        llm_selected_targets=llm_selected_targets,
    )

    rows: List[BenchmarkRow] = []
    for sample_count in sample_sizes:
        sample_indices = sample_order[:sample_count]
        vectors = np.ascontiguousarray(all_vectors[sample_indices])
        sample_llm_selected_targets: Optional[Dict[int, List[int]]] = None
        if sample_count == all_vectors.shape[0] and llm_selected_targets:
            local_index_by_global_index = np.empty(all_vectors.shape[0], dtype=np.int32)
            local_index_by_global_index[sample_indices] = np.arange(sample_count, dtype=np.int32)
            sample_llm_selected_targets = {
                query_index: [
                    int(local_index_by_global_index[global_index]) for global_index in global_indices
                ]
                for query_index, global_indices in llm_selected_targets.items()
            }
        exact_similarities = queries @ vectors.T
        exact_top50 = exact_top_indices(exact_similarities, args.top_k)
        effective_pool_targets = sorted(
            {
                *(min(raw_pool_target, sample_count) for raw_pool_target in pool_targets),
                recommended_raw_pool_target(sample_count),
            }
        )
        for cluster_count in build_cluster_counts(sample_count, requested_cluster_counts):
            centers, labels, kmeans_seconds = train_clusters(
                vectors,
                cluster_count=cluster_count,
                iterations=args.kmeans_iterations,
                seed=args.seed,
            )
            for raw_pool_target in effective_pool_targets:
                rows.append(
                    evaluate_route(
                        vectors,
                        queries,
                        centers,
                        labels,
                        exact_similarities,
                        exact_top50,
                        cluster_pool_minimum=args.cluster_pool_minimum,
                        raw_pool_target=raw_pool_target,
                        kmeans_seconds=kmeans_seconds,
                        llm_selected_targets=sample_llm_selected_targets,
                    )
                )

    formula_rows: List[dict[str, Any]] = []
    highlighted_targets = sorted({0, min(2500, max(pool_targets))})
    for sample_count in sample_sizes:
        for formula_name, cluster_count in formula_cluster_counts(sample_count).items():
            for raw_pool_target in highlighted_targets:
                row = find_row(
                    rows,
                    sample_count=sample_count,
                    cluster_count=cluster_count,
                    raw_pool_target=min(raw_pool_target, sample_count),
                )
                formula_rows.append({"formula": formula_name, **asdict(row)})

    recommended_rows = [
        asdict(
            find_row(
                rows,
                sample_count=sample_count,
                cluster_count=recommended_cluster_count(sample_count),
                raw_pool_target=recommended_raw_pool_target(sample_count),
            )
        )
        for sample_count in sample_sizes
    ]
    recommendations = choose_recommendations(rows, sample_sizes)
    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "index_json": str(index_path),
        "query_npz": str(query_path),
        "expression_count": all_vectors.shape[0],
        "query_count": queries.shape[0],
        "llm_selected_query_count": len(llm_selected_targets),
        "llm_selected_target_count": sum(len(values) for values in llm_selected_targets.values()),
        "embedding_dimension": all_vectors.shape[1],
        "index_embedding_model": index_payload.get("embedding_model"),
        "query_embedding_model": query_model_name,
        "top_k": args.top_k,
        "cluster_pool_minimum": args.cluster_pool_minimum,
        "sample_sizes": sample_sizes,
        "pool_targets": pool_targets,
        "seed": args.seed,
        "kmeans_iterations": args.kmeans_iterations,
    }
    report = {
        "metadata": metadata,
        "current_index_baseline": asdict(current_baseline),
        "exact_full_scan_prefilter": asdict(exact_prefilter),
        "formula_comparison": formula_rows,
        "recommended_dynamic_standard": {
            "cluster_count_formula": "min(640, max(32, ceil(N / 28)))",
            "raw_pool_target_formula": (
                "min(2500, N / 2, 500 * max(1, floor(log2(N / 500))))"
            ),
            "rows": recommended_rows,
        },
        "recommendations_under_recall_constraint": recommendations,
        "rows": [asdict(row) for row in rows],
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md_path.write_text(
        render_markdown(
            metadata=metadata,
            current_baseline=current_baseline,
            exact_prefilter=exact_prefilter,
            formula_rows=formula_rows,
            recommended_rows=recommended_rows,
            recommendations=recommendations,
        ),
        encoding="utf-8",
    )
    print(f"评估完成：{len(rows)} 组参数")
    print(f"JSON：{output_json_path}")
    print(f"Markdown：{output_md_path}")


if __name__ == "__main__":
    main()

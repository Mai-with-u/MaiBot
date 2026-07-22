from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import asyncio
import json
import re
import time

import numpy as np

from src.common.logger import get_logger

logger = get_logger("expression_vector_index")
PROJECT_ROOT = Path(__file__).resolve().parents[3]

VECTOR_CANDIDATE_HARD_LIMIT = 50
VECTOR_INDEX_VERSION = 2
VECTOR_ITEM_WEIGHT = 0.7
VECTOR_CLUSTER_WEIGHT = 0.1
VECTOR_LEXICAL_WEIGHT = 0.2
VECTOR_DIVERSITY_LAMBDA = 0.85
EMBEDDING_PROFILE_CACHE_SECONDS = 600.0
EMBEDDING_PROFILE_VERSION = 2
EMBEDDING_PROFILE_MIN_COSINE_SIMILARITY = 0.999
EMBEDDING_PROFILE_DRIFT_CONFIRMATIONS = 3
LEGACY_EMBEDDING_PROFILE_MARKER = "__legacy_unmarked__"
HISTORY_BACKFILL_BATCH_SIZE = 200
HISTORY_BACKFILL_MIN_INTERVAL_SECONDS = 10.0
HISTORY_BACKFILL_MAX_INTERVAL_SECONDS = 600.0
HISTORY_BACKFILL_INTERVAL_SPEED_RATIO = 1.0
HISTORY_BACKFILL_EMPTY_SCAN_INTERVAL_SECONDS = 300.0
EMBEDDING_PROFILE_PROBE_TEXTS = [
    "MaiBot 表达检索 embedding profile probe v1：技术问题排查、报错截图、配置异常",
    "MaiBot 表达检索 embedding profile probe v1：轻松吐槽、接梗、日常群聊",
    "MaiBot 表达检索 embedding profile probe v1：情绪回应、安慰、拒绝、调侃",
]


@dataclass(frozen=True)
class ExpressionEmbeddingProfile:
    """一次 embedding 后端实际行为的稳定标记。"""

    marker: str
    model_name: str
    model_identifier: str
    api_provider: str
    dimension: int
    revision: int
    probe_embeddings: Tuple[Tuple[float, ...], ...]


@dataclass(frozen=True)
class IndexedExpression:
    """向量索引中的表达方式记录。"""

    id: int
    situation: str
    style: str
    count: int
    fingerprint: str
    embedding_profile_marker: str
    embedding_model: str
    embedding_dimension: int
    cluster_id: int
    index: int


@dataclass(frozen=True)
class ExpressionVectorIndexSnapshot:
    """一次加载完成的表达方式向量索引。"""

    path: Path
    mtime: float
    embedding_model: str
    expressions: List[IndexedExpression]
    profile_vectors: Dict[str, np.ndarray]
    profile_cluster_centers: Dict[str, np.ndarray]


@dataclass(frozen=True)
class ExpressionVectorIndexUpsertItem:
    """需要写入表达向量索引的一条表达方式。"""

    id: int
    situation: str
    style: str
    count: int
    session_id: str | None
    checked: bool
    modified_by: str


def normalize_text(value: Any) -> str:
    """压缩空白并去除首尾空白。"""

    return " ".join(str(value or "").split()).strip()


def expression_fingerprint(expression_id: int, situation: str, style: str) -> str:
    """生成用于判断索引是否仍匹配当前表达内容的指纹。"""

    raw_text = f"{int(expression_id)}\n{normalize_text(situation)}\n{normalize_text(style)}"
    return sha256(raw_text.encode("utf-8")).hexdigest()


def expression_embedding_text(situation: str, style: str) -> str:
    """构建表达方式候选的 embedding 文本。"""

    return f"情景：{normalize_text(situation)}\n风格：{normalize_text(style)}"


def _build_embedding_profile_marker(
    *,
    model_name: str,
    model_identifier: str,
    api_provider: str,
    dimension: int,
    revision: int,
) -> str:
    """使用稳定后端身份和向量空间修订号生成 profile marker。"""

    payload = {
        "version": EMBEDDING_PROFILE_VERSION,
        "model_name": model_name,
        "model_identifier": model_identifier,
        "api_provider": api_provider,
        "dimension": int(dimension),
        "revision": int(revision),
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _build_embedding_profile(
    *,
    model_name: str,
    model_identifier: str,
    api_provider: str,
    probe_embeddings: Sequence[Sequence[float]],
    revision: int,
) -> ExpressionEmbeddingProfile:
    """根据稳定身份、探针基准和修订号构建 embedding profile。"""

    normalized_probes = tuple(tuple(float(value) for value in embedding) for embedding in probe_embeddings)
    dimensions = {len(embedding) for embedding in normalized_probes}
    if len(dimensions) != 1:
        raise ValueError(f"embedding profile 探针维度不一致: {sorted(dimensions)}")
    dimension = next(iter(dimensions))
    if dimension <= 0:
        raise ValueError("embedding profile 探针返回空向量")
    if revision <= 0:
        raise ValueError(f"embedding profile 修订号无效: {revision}")

    marker = _build_embedding_profile_marker(
        model_name=model_name,
        model_identifier=model_identifier,
        api_provider=api_provider,
        dimension=dimension,
        revision=revision,
    )
    return ExpressionEmbeddingProfile(
        marker=marker,
        model_name=model_name,
        model_identifier=model_identifier,
        api_provider=api_provider,
        dimension=dimension,
        revision=revision,
        probe_embeddings=normalized_probes,
    )


def build_embedding_profile_from_probe_results(results: Sequence[Any]) -> ExpressionEmbeddingProfile:
    """根据固定探针 embedding 结果生成当前 embedding profile。"""

    if len(results) != len(EMBEDDING_PROFILE_PROBE_TEXTS):
        raise ValueError(
            f"embedding profile 探针数量异常: results={len(results)}, probes={len(EMBEDDING_PROFILE_PROBE_TEXTS)}"
        )

    model_names = {normalize_text(result.model_name) for result in results}
    if len(model_names) != 1 or not all(model_names):
        raise ValueError(f"embedding profile 探针命中模型不一致: {sorted(model_names)}")
    model_name = next(iter(model_names))

    model_identifiers = {normalize_text(result.model_identifier) for result in results}
    if len(model_identifiers) != 1 or not all(model_identifiers):
        raise ValueError(f"embedding profile 探针命中模型标识不一致: {sorted(model_identifiers)}")
    model_identifier = next(iter(model_identifiers))

    api_providers = {normalize_text(result.api_provider) for result in results}
    if len(api_providers) != 1 or not all(api_providers):
        raise ValueError(f"embedding profile 探针命中 Provider 不一致: {sorted(api_providers)}")
    api_provider = next(iter(api_providers))

    return _build_embedding_profile(
        model_name=model_name,
        model_identifier=model_identifier,
        api_provider=api_provider,
        probe_embeddings=[result.embedding for result in results],
        revision=1,
    )


def _embedding_profile_identity(profile: ExpressionEmbeddingProfile) -> Tuple[str, str, str, int]:
    """返回用于明确区分 embedding 后端的稳定身份。"""

    return (
        profile.model_name,
        profile.model_identifier,
        profile.api_provider,
        profile.dimension,
    )


def _embedding_profile_probe_similarities(
    baseline: ExpressionEmbeddingProfile,
    candidate: ExpressionEmbeddingProfile,
) -> List[float]:
    """计算两次 profile 标定中对应探针的余弦相似度。"""

    if len(baseline.probe_embeddings) != len(candidate.probe_embeddings):
        raise ValueError(
            "embedding profile 探针数量不一致: "
            f"baseline={len(baseline.probe_embeddings)}, candidate={len(candidate.probe_embeddings)}"
        )
    if baseline.dimension != candidate.dimension:
        raise ValueError(
            f"embedding profile 探针维度不一致: baseline={baseline.dimension}, candidate={candidate.dimension}"
        )

    similarities: List[float] = []
    for baseline_embedding, candidate_embedding in zip(
        baseline.probe_embeddings,
        candidate.probe_embeddings,
        strict=True,
    ):
        baseline_vector = np.asarray(baseline_embedding, dtype=np.float64)
        candidate_vector = np.asarray(candidate_embedding, dtype=np.float64)
        baseline_norm = float(np.linalg.norm(baseline_vector))
        candidate_norm = float(np.linalg.norm(candidate_vector))
        if baseline_norm <= 0 or candidate_norm <= 0:
            raise ValueError("embedding profile 探针包含零向量，无法判断向量空间兼容性")
        similarity = float(np.dot(baseline_vector, candidate_vector) / (baseline_norm * candidate_norm))
        similarities.append(max(-1.0, min(1.0, similarity)))
    return similarities


def _embedding_profiles_are_compatible(
    baseline: ExpressionEmbeddingProfile,
    candidate: ExpressionEmbeddingProfile,
) -> bool:
    """判断候选 profile 是否仍与基准向量空间兼容。"""

    if _embedding_profile_identity(baseline) != _embedding_profile_identity(candidate):
        return False
    similarities = _embedding_profile_probe_similarities(baseline, candidate)
    return all(similarity >= EMBEDDING_PROFILE_MIN_COSINE_SIMILARITY for similarity in similarities)


def _serialize_embedding_profile(profile: ExpressionEmbeddingProfile) -> dict[str, Any]:
    """把当前 profile 及其探针基准写入索引元数据。"""

    return {
        "version": EMBEDDING_PROFILE_VERSION,
        "marker": profile.marker,
        "model_name": profile.model_name,
        "model_identifier": profile.model_identifier,
        "api_provider": profile.api_provider,
        "dimension": profile.dimension,
        "revision": profile.revision,
        "probe_texts": list(EMBEDDING_PROFILE_PROBE_TEXTS),
        "probe_embeddings": [list(embedding) for embedding in profile.probe_embeddings],
    }


def _deserialize_embedding_profile(raw_profile: dict[str, Any]) -> ExpressionEmbeddingProfile:
    """从索引元数据恢复并校验持久化的 profile 基准。"""

    version = int(raw_profile.get("version") or 0)
    if version != EMBEDDING_PROFILE_VERSION:
        raise ValueError(f"embedding profile 元数据版本不匹配: {version}")
    probe_texts = raw_profile.get("probe_texts")
    if probe_texts != EMBEDDING_PROFILE_PROBE_TEXTS:
        raise ValueError("embedding profile 探针文本与当前版本不一致")
    raw_probe_embeddings = raw_profile.get("probe_embeddings")
    if not isinstance(raw_probe_embeddings, list) or len(raw_probe_embeddings) != len(EMBEDDING_PROFILE_PROBE_TEXTS):
        raise ValueError("embedding profile 持久化探针数量异常")

    profile = _build_embedding_profile(
        model_name=normalize_text(raw_profile.get("model_name")),
        model_identifier=normalize_text(raw_profile.get("model_identifier")),
        api_provider=normalize_text(raw_profile.get("api_provider")),
        probe_embeddings=raw_probe_embeddings,
        revision=int(raw_profile.get("revision") or 0),
    )
    if not all((profile.model_name, profile.model_identifier, profile.api_provider)):
        raise ValueError("embedding profile 持久化后端身份为空")
    stored_dimension = int(raw_profile.get("dimension") or 0)
    if stored_dimension != profile.dimension:
        raise ValueError(
            f"embedding profile 持久化维度不一致: stored={stored_dimension}, actual={profile.dimension}"
        )
    stored_marker = normalize_text(raw_profile.get("marker"))
    if stored_marker != profile.marker:
        raise ValueError(
            f"embedding profile 持久化 marker 不一致: stored={stored_marker[:12]}, actual={profile.marker[:12]}"
        )
    return profile


def resolve_project_path(raw_path: str) -> Path:
    """解析项目内路径。"""

    path = Path(normalize_text(raw_path)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """按行执行 L2 归一化。"""

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("表达向量索引包含零向量，无法用于余弦检索")
    return matrix / norms


def lexical_tokens(text: str) -> set[str]:
    """把中英文混合文本切成轻量词面 token。"""

    normalized = normalize_text(text).lower()
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9_#+.-]{2,}", normalized):
        tokens.add(word)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    tokens.update(cjk_chars)
    for index in range(len(cjk_chars) - 1):
        tokens.add("".join(cjk_chars[index : index + 2]))
    return tokens


def lexical_overlap_score(query_tokens: set[str], candidate: IndexedExpression) -> float:
    """计算 query 与候选 situation/style 的通用词面重合分。"""

    if not query_tokens:
        return 0.0
    candidate_tokens = lexical_tokens(f"{candidate.situation}\n{candidate.style}")
    if not candidate_tokens:
        return 0.0
    overlap_count = len(query_tokens & candidate_tokens)
    if overlap_count <= 0:
        return 0.0
    return overlap_count / max(1.0, len(query_tokens) ** 0.5 * len(candidate_tokens) ** 0.5)


def _load_npz_array(npz_path: Path, key: str) -> np.ndarray:
    """从 npz 中读取指定数组，并给出清晰错误。"""

    with np.load(npz_path) as payload:
        if key not in payload:
            raise ValueError(f"表达向量索引缺少数组: {key}")
        return np.array(payload[key], dtype=np.float32)


def _resolve_vectors_path(index_path: Path, payload: dict[str, Any]) -> Path:
    """解析索引 payload 中记录的向量文件路径。"""

    raw_vectors_path = normalize_text(payload.get("vectors_file"))
    if not raw_vectors_path:
        return index_path.with_suffix(".npz")
    vectors_path = Path(raw_vectors_path)
    if not vectors_path.is_absolute():
        vectors_path = index_path.parent / vectors_path
    return vectors_path.resolve()


def _atomic_write_text(path: Path, content: str) -> None:
    """用同目录临时文件原子替换文本文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.stem}.tmp{path.suffix}")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


class ExpressionVectorIndex:
    """表达方式向量索引运行时加载器。"""

    def __init__(self) -> None:
        self._snapshot: ExpressionVectorIndexSnapshot | None = None
        self._update_lock = asyncio.Lock()
        self._profile_lock = asyncio.Lock()
        self._profile_cache: tuple[float, ExpressionEmbeddingProfile] | None = None
        self._profile_drift_candidate: ExpressionEmbeddingProfile | None = None
        self._profile_drift_confirmations = 0
        self._history_backfill_task: asyncio.Task[None] | None = None
        self._history_backfill_last_empty_at = 0.0

    @staticmethod
    def _load_persisted_embedding_profile(index_path: Path) -> ExpressionEmbeddingProfile | None:
        """从现有索引读取当前向量空间的持久化探针基准。"""

        if not index_path.exists():
            return None
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        raw_profile = payload.get("embedding_profile")
        if raw_profile is None:
            return None
        if not isinstance(raw_profile, dict):
            raise ValueError("表达向量索引中的 embedding_profile 元数据格式错误")
        return _deserialize_embedding_profile(raw_profile)

    def _reset_profile_drift_candidate(self) -> None:
        """清理尚未达到确认次数的向量空间漂移候选。"""

        self._profile_drift_candidate = None
        self._profile_drift_confirmations = 0

    def _resolve_embedding_profile_candidate(
        self,
        *,
        persisted_profile: ExpressionEmbeddingProfile | None,
        candidate_profile: ExpressionEmbeddingProfile,
    ) -> ExpressionEmbeddingProfile:
        """根据持久化基准和连续确认结果解析本次实际使用的 profile。"""

        if persisted_profile is None:
            self._reset_profile_drift_candidate()
            return candidate_profile

        if _embedding_profile_identity(persisted_profile) != _embedding_profile_identity(candidate_profile):
            self._reset_profile_drift_candidate()
            return candidate_profile

        similarities = _embedding_profile_probe_similarities(persisted_profile, candidate_profile)
        min_similarity = min(similarities)
        if min_similarity >= EMBEDDING_PROFILE_MIN_COSINE_SIMILARITY:
            self._reset_profile_drift_candidate()
            return persisted_profile

        if self._profile_drift_candidate is not None and _embedding_profiles_are_compatible(
            self._profile_drift_candidate,
            candidate_profile,
        ):
            self._profile_drift_confirmations += 1
        else:
            self._profile_drift_candidate = candidate_profile
            self._profile_drift_confirmations = 1

        if self._profile_drift_confirmations < EMBEDDING_PROFILE_DRIFT_CONFIRMATIONS:
            logger.warning(
                "检测到 embedding 向量空间疑似漂移，等待连续确认: "
                f"min_cosine={min_similarity:.8f} "
                f"confirmations={self._profile_drift_confirmations}/{EMBEDDING_PROFILE_DRIFT_CONFIRMATIONS}"
            )
            return persisted_profile

        next_profile = _build_embedding_profile(
            model_name=candidate_profile.model_name,
            model_identifier=candidate_profile.model_identifier,
            api_provider=candidate_profile.api_provider,
            probe_embeddings=candidate_profile.probe_embeddings,
            revision=persisted_profile.revision + 1,
        )
        logger.warning(
            "embedding 向量空间漂移已连续确认，切换 profile: "
            f"old_marker={persisted_profile.marker[:12]} new_marker={next_profile.marker[:12]} "
            f"min_cosine={min_similarity:.8f} revision={next_profile.revision}"
        )
        self._reset_profile_drift_candidate()
        return next_profile

    async def get_current_embedding_profile(
        self,
        *,
        index_path: str,
        session_id: str = "",
    ) -> ExpressionEmbeddingProfile:
        """用固定探针解析当前 embedding 后端 profile，并做短时缓存。"""

        now = time.monotonic()
        if self._profile_cache is not None:
            cached_at, cached_profile = self._profile_cache
            if now - cached_at <= EMBEDDING_PROFILE_CACHE_SECONDS:
                return cached_profile

        async with self._profile_lock:
            now = time.monotonic()
            if self._profile_cache is not None:
                cached_at, cached_profile = self._profile_cache
                if now - cached_at <= EMBEDDING_PROFILE_CACHE_SECONDS:
                    return cached_profile

            from src.services.embedding_service import EmbeddingServiceClient

            embedding_client = EmbeddingServiceClient(
                task_name="embedding",
                request_type="expression.selection.profile_probe",
                session_id=session_id,
            )
            probe_results = await embedding_client.embed_texts(
                list(EMBEDDING_PROFILE_PROBE_TEXTS),
                max_concurrent=1,
                session_id=session_id,
            )
            candidate_profile = build_embedding_profile_from_probe_results(probe_results)
            persisted_profile = self._load_persisted_embedding_profile(resolve_project_path(index_path))
            profile = self._resolve_embedding_profile_candidate(
                persisted_profile=persisted_profile,
                candidate_profile=candidate_profile,
            )
            self._profile_cache = (time.monotonic(), profile)
            logger.info(
                f"表达向量 embedding profile 已标定: marker={profile.marker[:12]} "
                f"model={profile.model_name} identifier={profile.model_identifier} "
                f"provider={profile.api_provider} dimension={profile.dimension} revision={profile.revision}"
            )
            return profile

    @staticmethod
    def _validate_embedding_result_profile(
        result: Any,
        profile: ExpressionEmbeddingProfile,
        *,
        usage: str,
    ) -> None:
        result_model_name = normalize_text(result.model_name)
        result_model_identifier = normalize_text(result.model_identifier)
        result_api_provider = normalize_text(result.api_provider)
        result_dimension = len(result.embedding)
        if (
            result_model_name != profile.model_name
            or result_model_identifier != profile.model_identifier
            or result_api_provider != profile.api_provider
            or result_dimension != profile.dimension
        ):
            raise ValueError(
                f"{usage} embedding profile 与当前标定不一致: "
                f"result_model={result_model_name!r}, profile_model={profile.model_name!r}, "
                f"result_identifier={result_model_identifier!r}, "
                f"profile_identifier={profile.model_identifier!r}, "
                f"result_provider={result_api_provider!r}, profile_provider={profile.api_provider!r}, "
                f"result_dimension={result_dimension}, profile_dimension={profile.dimension}"
            )

    def _load_snapshot(self, index_path: Path) -> ExpressionVectorIndexSnapshot | None:
        """按 mtime 缓存并加载索引文件。"""

        if not index_path.exists():
            logger.warning(f"表达向量索引不存在，跳过向量召回: {index_path}")
            return None

        mtime = index_path.stat().st_mtime
        if self._snapshot is not None and self._snapshot.path == index_path and self._snapshot.mtime == mtime:
            return self._snapshot

        payload = json.loads(index_path.read_text(encoding="utf-8"))
        payload_version = int(payload.get("version") or 0)
        if payload_version < 1 or payload_version > VECTOR_INDEX_VERSION:
            raise ValueError(f"表达向量索引版本不匹配: {payload.get('version')!r}")

        vectors_path = _resolve_vectors_path(index_path, payload)

        profile_vectors: Dict[str, np.ndarray] = {}
        profile_cluster_centers: Dict[str, np.ndarray] = {}
        raw_profiles = payload.get("embedding_profiles")
        if isinstance(raw_profiles, list) and raw_profiles:
            for raw_profile in raw_profiles:
                if not isinstance(raw_profile, dict):
                    continue
                marker = normalize_text(raw_profile.get("marker"))
                vectors_key = normalize_text(raw_profile.get("vectors_key"))
                cluster_centers_key = normalize_text(raw_profile.get("cluster_centers_key"))
                if not marker or not vectors_key or not cluster_centers_key:
                    continue
                vectors = l2_normalize(_load_npz_array(vectors_path, vectors_key))
                cluster_centers = l2_normalize(_load_npz_array(vectors_path, cluster_centers_key))
                if cluster_centers.ndim != 2 or vectors.ndim != 2 or cluster_centers.shape[1] != vectors.shape[1]:
                    raise ValueError(
                        f"表达向量索引 profile 维度异常: marker={marker[:12]} "
                        f"vectors={vectors.shape}, cluster_centers={cluster_centers.shape}"
                    )
                profile_vectors[marker] = vectors
                profile_cluster_centers[marker] = cluster_centers
        else:
            legacy_marker = normalize_text(payload.get("embedding_profile_marker")) or LEGACY_EMBEDDING_PROFILE_MARKER
            profile_vectors[legacy_marker] = l2_normalize(_load_npz_array(vectors_path, "vectors"))
            profile_cluster_centers[legacy_marker] = l2_normalize(_load_npz_array(vectors_path, "cluster_centers"))

        expressions: List[IndexedExpression] = []
        for index, raw_expression in enumerate(payload.get("expressions") or []):
            if not isinstance(raw_expression, dict):
                continue
            expression_id = int(raw_expression.get("id") or 0)
            situation = normalize_text(raw_expression.get("situation"))
            style = normalize_text(raw_expression.get("style"))
            if expression_id <= 0 or not situation or not style:
                continue
            cluster_id = int(raw_expression.get("cluster_id") or 0)
            profile_marker = (
                normalize_text(raw_expression.get("embedding_profile_marker") or payload.get("embedding_profile_marker"))
                or LEGACY_EMBEDDING_PROFILE_MARKER
            )
            profile_vectors_for_marker = profile_vectors.get(profile_marker)
            vector_index = int(raw_expression.get("vector_index") if "vector_index" in raw_expression else index)
            if profile_vectors_for_marker is None:
                continue
            if vector_index < 0 or vector_index >= profile_vectors_for_marker.shape[0]:
                raise ValueError(
                    f"表达向量索引 vector_index 越界: id={expression_id}, marker={profile_marker[:12]}, "
                    f"vector_index={vector_index}, vectors={profile_vectors_for_marker.shape[0]}"
                )
            expressions.append(
                IndexedExpression(
                    id=expression_id,
                    situation=situation,
                    style=style,
                    count=int(raw_expression.get("count") or 0),
                    fingerprint=normalize_text(raw_expression.get("fingerprint")),
                    embedding_profile_marker=profile_marker,
                    embedding_model=normalize_text(raw_expression.get("embedding_model") or payload.get("embedding_model")),
                    embedding_dimension=int(
                        raw_expression.get("embedding_dimension")
                        or profile_vectors_for_marker.shape[1]
                    ),
                    cluster_id=cluster_id,
                    index=vector_index,
                )
            )

        self._snapshot = ExpressionVectorIndexSnapshot(
            path=index_path,
            mtime=mtime,
            embedding_model=normalize_text(payload.get("embedding_model")),
            expressions=expressions,
            profile_vectors=profile_vectors,
            profile_cluster_centers=profile_cluster_centers,
        )
        profile_summary = ", ".join(
            f"{marker[:12] or 'legacy'}:{vectors.shape[0]}x{vectors.shape[1]}"
            for marker, vectors in profile_vectors.items()
        )
        logger.info(
            f"表达向量索引已加载: path={index_path} count={len(expressions)} "
            f"profiles=[{profile_summary}]"
        )
        return self._snapshot

    @staticmethod
    def _filter_indexed_expressions(
        snapshot: ExpressionVectorIndexSnapshot,
        scoped_candidates: Sequence[dict[str, Any]],
        profile: ExpressionEmbeddingProfile,
    ) -> List[IndexedExpression]:
        """只保留当前聊天流可用且内容未变化的索引候选。"""

        scoped_by_id: Dict[int, dict[str, Any]] = {
            int(candidate["id"]): candidate
            for candidate in scoped_candidates
            if isinstance(candidate.get("id"), int)
        }
        filtered: List[IndexedExpression] = []
        for indexed_expression in snapshot.expressions:
            if indexed_expression.embedding_profile_marker != profile.marker:
                continue
            if indexed_expression.embedding_dimension != profile.dimension:
                continue
            scoped_candidate = scoped_by_id.get(indexed_expression.id)
            if scoped_candidate is None:
                continue
            situation = normalize_text(scoped_candidate.get("situation"))
            style = normalize_text(scoped_candidate.get("style"))
            fingerprint = expression_fingerprint(indexed_expression.id, situation, style)
            if indexed_expression.fingerprint and indexed_expression.fingerprint != fingerprint:
                continue
            filtered.append(
                IndexedExpression(
                    id=indexed_expression.id,
                    situation=situation,
                    style=style,
                    count=int(scoped_candidate.get("count") or indexed_expression.count or 0),
                    fingerprint=fingerprint,
                    embedding_profile_marker=indexed_expression.embedding_profile_marker,
                    embedding_model=indexed_expression.embedding_model,
                    embedding_dimension=indexed_expression.embedding_dimension,
                    cluster_id=indexed_expression.cluster_id,
                    index=indexed_expression.index,
                )
            )
        return filtered

    @staticmethod
    def _select_by_mmr(
        scored_candidates: List[dict[str, Any]],
        vectors: np.ndarray,
        *,
        limit: int,
    ) -> List[dict[str, Any]]:
        """用轻量 MMR 避免候选池过度集中在同一类表达。"""

        if VECTOR_DIVERSITY_LAMBDA >= 0.999:
            return sorted(scored_candidates, key=lambda item: float(item["score"]), reverse=True)[:limit]

        selected: List[dict[str, Any]] = []
        remaining = list(scored_candidates)
        while remaining and len(selected) < limit:
            selected_indices = [int(item["vector_index"]) for item in selected]
            best_index = 0
            best_score = float("-inf")
            for candidate_index, candidate in enumerate(remaining):
                vector_index = int(candidate["vector_index"])
                if selected_indices:
                    diversity_penalty = float(np.max(vectors[selected_indices] @ vectors[vector_index]))
                else:
                    diversity_penalty = 0.0
                mmr_score = VECTOR_DIVERSITY_LAMBDA * float(candidate["score"]) - (
                    1.0 - VECTOR_DIVERSITY_LAMBDA
                ) * diversity_penalty
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_index = candidate_index
            selected.append(remaining.pop(best_index))
        return selected

    @staticmethod
    def _build_index_expression_item(
        *,
        expression_id: int,
        situation: str,
        style: str,
        count: int,
        session_id: str | None,
        checked: bool,
        modified_by: str,
        embedding_profile_marker: str,
        embedding_model: str,
        embedding_dimension: int,
        vector_index: int,
        cluster_id: int,
    ) -> dict[str, Any]:
        """构建写入索引 JSON 的表达记录。"""

        normalized_situation = normalize_text(situation)
        normalized_style = normalize_text(style)
        return {
            "id": int(expression_id),
            "situation": normalized_situation,
            "style": normalized_style,
            "count": int(count),
            "session_id": normalize_text(session_id) or None,
            "checked": bool(checked),
            "modified_by": normalize_text(modified_by),
            "fingerprint": expression_fingerprint(int(expression_id), normalized_situation, normalized_style),
            "embedding_profile_marker": normalize_text(embedding_profile_marker),
            "embedding_model": normalize_text(embedding_model),
            "embedding_dimension": int(embedding_dimension),
            "vector_index": int(vector_index),
            "cluster_id": int(cluster_id),
        }

    @staticmethod
    def _load_raw_index_expressions(index_path: Path) -> Dict[int, dict[str, Any]]:
        """读取索引 JSON 中的表达元数据，用于判断历史表达是否需要补建。"""

        if not index_path.exists():
            return {}

        payload = json.loads(index_path.read_text(encoding="utf-8"))
        payload_version = int(payload.get("version") or 0)
        if payload_version < 1 or payload_version > VECTOR_INDEX_VERSION:
            raise ValueError(f"表达向量索引版本不匹配: {payload.get('version')!r}")

        indexed_by_id: Dict[int, dict[str, Any]] = {}
        for raw_expression in payload.get("expressions") or []:
            if not isinstance(raw_expression, dict):
                continue
            expression_id = int(raw_expression.get("id") or 0)
            if expression_id <= 0:
                continue
            indexed_by_id[expression_id] = raw_expression
        return indexed_by_id

    @staticmethod
    def _needs_history_backfill(
        *,
        indexed_expression: dict[str, Any] | None,
        expression_id: int,
        situation: str,
        style: str,
        profile: ExpressionEmbeddingProfile,
    ) -> bool:
        """判断数据库表达是否缺失当前 profile 的可用向量。"""

        if indexed_expression is None:
            return True
        if normalize_text(indexed_expression.get("embedding_profile_marker")) != profile.marker:
            return True
        if int(indexed_expression.get("embedding_dimension") or 0) != profile.dimension:
            return True
        fingerprint = expression_fingerprint(expression_id, situation, style)
        return normalize_text(indexed_expression.get("fingerprint")) != fingerprint

    def _load_history_backfill_items(
        self,
        *,
        index_path: Path,
        profile: ExpressionEmbeddingProfile,
        batch_size: int,
    ) -> List[ExpressionVectorIndexUpsertItem]:
        """从数据库读取一批缺失或过期的历史表达。"""

        from sqlmodel import select

        from src.common.database.database import get_db_session
        from src.common.database.database_model import Expression, ModifiedBy

        indexed_by_id = self._load_raw_index_expressions(index_path)
        items: List[ExpressionVectorIndexUpsertItem] = []
        with get_db_session(auto_commit=False) as session:
            statement = (
                select(
                    Expression.id,
                    Expression.situation,
                    Expression.style,
                    Expression.count,
                    Expression.session_id,
                    Expression.checked,
                    Expression.modified_by,
                )
                .order_by(Expression.id)
            )
            rows = session.exec(statement).all()

        for row in rows:
            expression_id, situation, style, count, session_id, checked, modified_by = row
            if expression_id is None:
                continue
            normalized_situation = normalize_text(situation)
            normalized_style = normalize_text(style)
            if not normalized_situation or not normalized_style:
                continue
            if not self._needs_history_backfill(
                indexed_expression=indexed_by_id.get(int(expression_id)),
                expression_id=int(expression_id),
                situation=normalized_situation,
                style=normalized_style,
                profile=profile,
            ):
                continue

            modified_by_text = modified_by.value if isinstance(modified_by, ModifiedBy) else normalize_text(modified_by)
            items.append(
                ExpressionVectorIndexUpsertItem(
                    id=int(expression_id),
                    situation=normalized_situation,
                    style=normalized_style,
                    count=int(count or 0),
                    session_id=normalize_text(session_id) or None,
                    checked=bool(checked),
                    modified_by=modified_by_text,
                )
            )
            if len(items) >= batch_size:
                break
        return items

    @staticmethod
    def _load_current_expression_fingerprints() -> Dict[int, str]:
        """读取当前数据库中仍有效的表达方式指纹，用于清理过期索引项。"""

        from sqlmodel import select

        from src.common.database.database import get_db_session
        from src.common.database.database_model import Expression

        fingerprints: Dict[int, str] = {}
        with get_db_session(auto_commit=False) as session:
            rows = session.exec(
                select(
                    Expression.id,
                    Expression.situation,
                    Expression.style,
                )
            ).all()

        for expression_id, situation, style in rows:
            if expression_id is None:
                continue
            normalized_situation = normalize_text(situation)
            normalized_style = normalize_text(style)
            if not normalized_situation or not normalized_style:
                continue
            fingerprints[int(expression_id)] = expression_fingerprint(
                int(expression_id),
                normalized_situation,
                normalized_style,
            )
        return fingerprints

    @staticmethod
    def _is_current_index_expression(
        raw_expression: dict[str, Any],
        current_fingerprints: Dict[int, str],
    ) -> bool:
        """判断索引表达项是否仍与当前数据库记录一致。"""

        expression_id = int(raw_expression.get("id") or 0)
        if expression_id <= 0:
            return False
        expected_fingerprint = current_fingerprints.get(expression_id)
        if not expected_fingerprint:
            return False
        return normalize_text(raw_expression.get("fingerprint")) == expected_fingerprint

    @staticmethod
    def _select_nearest_cluster(vector: np.ndarray, cluster_centers: np.ndarray) -> int:
        """根据当前聚类中心给新增/更新表达分配最近簇。"""

        if cluster_centers.size == 0:
            return 0
        normalized_centers = l2_normalize(cluster_centers)
        return int(np.argmax(normalized_centers @ vector))

    @staticmethod
    def _choose_cluster_count(sample_count: int, previous_cluster_count: int) -> int:
        """解析本次批量重聚类使用的簇数量。"""

        if sample_count <= 1:
            return 1
        if previous_cluster_count > 0:
            return max(1, min(int(previous_cluster_count), sample_count))
        return max(2, min(80, sample_count))

    @staticmethod
    def _repair_empty_cluster_labels(
        labels: np.ndarray,
        similarities: np.ndarray,
        cluster_count: int,
    ) -> np.ndarray:
        """从成员充足的簇中迁移最不匹配的样本，确保每个簇都有成员。"""

        repaired_labels = labels.copy()
        member_counts = np.bincount(repaired_labels, minlength=cluster_count)
        assigned_similarities = similarities[np.arange(repaired_labels.shape[0]), repaired_labels]

        for empty_cluster_id in np.flatnonzero(member_counts == 0):
            donor_candidates = np.flatnonzero(member_counts[repaired_labels] > 1)
            if donor_candidates.size == 0:
                raise ValueError("表达向量索引无法为所有聚类分配成员")

            # 只从至少有两个成员的簇迁移，并优先选择最不匹配原中心的样本。
            donor_index = int(
                donor_candidates[np.argmin(assigned_similarities[donor_candidates])]
            )
            source_cluster_id = int(repaired_labels[donor_index])
            repaired_labels[donor_index] = int(empty_cluster_id)
            member_counts[source_cluster_id] -= 1
            member_counts[empty_cluster_id] += 1

        return repaired_labels

    @staticmethod
    def _run_kmeans(
        normalized_vectors: np.ndarray,
        *,
        cluster_count: int,
        seed: int = 20260621,
        max_iter: int = 100,
    ) -> np.ndarray:
        """在归一化向量上执行确定性 cosine k-means。"""

        sample_count = normalized_vectors.shape[0]
        if cluster_count > sample_count:
            raise ValueError(
                f"表达向量索引聚类数量超过样本数量: clusters={cluster_count}, samples={sample_count}"
            )
        if cluster_count <= 1 or sample_count <= 1:
            return np.zeros(sample_count, dtype=np.int32)

        rng = np.random.default_rng(seed)
        centroid_indices = [int(rng.integers(0, sample_count))]
        while len(centroid_indices) < cluster_count:
            selected = normalized_vectors[centroid_indices]
            similarity = normalized_vectors @ selected.T
            distance = 1.0 - np.max(similarity, axis=1)
            distance = np.maximum(distance, 0.0)
            distance[centroid_indices] = 0.0
            total_distance = float(distance.sum())
            if total_distance <= 0:
                remaining_indices = [index for index in range(sample_count) if index not in centroid_indices]
                centroid_indices.append(remaining_indices[0])
                continue
            probabilities = distance / total_distance
            centroid_indices.append(int(rng.choice(sample_count, p=probabilities)))

        centroids = normalized_vectors[centroid_indices].copy()
        labels = np.full(sample_count, -1, dtype=np.int32)
        for _ in range(max_iter):
            similarities = normalized_vectors @ centroids.T
            next_labels = np.argmax(similarities, axis=1).astype(np.int32)
            next_labels = ExpressionVectorIndex._repair_empty_cluster_labels(
                next_labels,
                similarities,
                cluster_count,
            )
            next_centroids = ExpressionVectorIndex._build_cluster_centers_from_labels(
                normalized_vectors,
                next_labels,
                cluster_count,
            )
            converged = np.array_equal(next_labels, labels)
            labels = next_labels
            centroids = next_centroids
            if converged:
                break

        member_counts = np.bincount(labels, minlength=cluster_count)
        if np.any(member_counts == 0):
            raise ValueError(f"表达向量索引聚类结果存在空簇: counts={member_counts.tolist()}")
        return labels

    @staticmethod
    def _build_cluster_centers_from_labels(
        normalized_vectors: np.ndarray,
        labels: np.ndarray,
        cluster_count: int,
    ) -> np.ndarray:
        """根据 k-means 标签计算中心向量。"""

        centers: List[np.ndarray] = []
        for cluster_id in range(cluster_count):
            member_vectors = normalized_vectors[labels == cluster_id]
            if len(member_vectors) == 0:
                raise ValueError(f"表达向量索引聚类 {cluster_id} 没有成员")
            center = member_vectors.mean(axis=0)
            norm = float(np.linalg.norm(center))
            if norm <= 0:
                raise ValueError(f"表达向量索引聚类 {cluster_id} 中心向量为零")
            centers.append((center / norm).astype(np.float32))
        return np.vstack(centers).astype(np.float32)

    @staticmethod
    def _rebuild_cluster_centers(
        vectors: np.ndarray,
        raw_expressions: Sequence[dict[str, Any]],
        previous_cluster_centers: np.ndarray,
    ) -> np.ndarray:
        """根据当前表达标签重算中心，空簇保留旧中心。"""

        if vectors.size == 0:
            return np.empty((0, 0), dtype=np.float32)

        max_expression_cluster_id = max(
            (int(raw_expression.get("cluster_id") or 0) for raw_expression in raw_expressions),
            default=0,
        )
        cluster_count = max(max_expression_cluster_id + 1, int(previous_cluster_centers.shape[0] or 0), 1)
        centers: List[np.ndarray] = []
        for cluster_id in range(cluster_count):
            member_indices = [
                index
                for index, raw_expression in enumerate(raw_expressions)
                if int(raw_expression.get("cluster_id") or 0) == cluster_id
            ]
            if member_indices:
                center = vectors[member_indices].mean(axis=0)
                norm = float(np.linalg.norm(center))
                if norm <= 0:
                    raise ValueError(f"表达向量索引聚类 {cluster_id} 中心向量为零")
                centers.append((center / norm).astype(np.float32))
                continue
            if cluster_id < previous_cluster_centers.shape[0]:
                centers.append(previous_cluster_centers[cluster_id].astype(np.float32))
            else:
                centers.append(vectors[0].astype(np.float32))
        return np.vstack(centers).astype(np.float32)

    @staticmethod
    def _build_cluster_summaries(raw_expressions: Sequence[dict[str, Any]]) -> List[dict[str, Any]]:
        """生成索引 JSON 中的轻量聚类摘要。"""

        summaries: List[dict[str, Any]] = []
        profile_cluster_keys = sorted(
            {
                (normalize_text(raw_expression.get("embedding_profile_marker")), int(raw_expression.get("cluster_id") or 0))
                for raw_expression in raw_expressions
            }
        )
        for profile_marker, cluster_id in profile_cluster_keys:
            members = [
                raw_expression
                for raw_expression in raw_expressions
                if normalize_text(raw_expression.get("embedding_profile_marker")) == profile_marker
                and int(raw_expression.get("cluster_id") or 0) == cluster_id
            ]
            summaries.append(
                {
                    "embedding_profile_marker": profile_marker,
                    "cluster_id": cluster_id,
                    "size": len(members),
                    "members": [
                        {
                            "id": int(member.get("id") or 0),
                            "situation": normalize_text(member.get("situation")),
                            "style": normalize_text(member.get("style")),
                            "count": int(member.get("count") or 0),
                        }
                        for member in members[:8]
                    ],
                }
            )
        return sorted(summaries, key=lambda item: int(item["size"]), reverse=True)

    def _rebuild_profile_arrays(
        self,
        *,
        raw_expressions: List[dict[str, Any]],
        vector_by_expression_id: Dict[int, np.ndarray],
        previous_profile_cluster_centers: Dict[str, np.ndarray],
    ) -> tuple[Dict[str, np.ndarray], Dict[str, np.ndarray], List[dict[str, Any]]]:
        """按 embedding profile 分组重建向量矩阵、聚类中心和 profile 元数据。"""

        grouped_expression_indices: Dict[str, List[int]] = {}
        for expression_index, raw_expression in enumerate(raw_expressions):
            profile_marker = (
                normalize_text(raw_expression.get("embedding_profile_marker")) or LEGACY_EMBEDDING_PROFILE_MARKER
            )
            raw_expression["embedding_profile_marker"] = profile_marker
            grouped_expression_indices.setdefault(profile_marker, []).append(expression_index)

        profile_vectors: Dict[str, np.ndarray] = {}
        profile_cluster_centers: Dict[str, np.ndarray] = {}
        profile_metadata: List[dict[str, Any]] = []
        for profile_index, profile_marker in enumerate(sorted(grouped_expression_indices)):
            expression_indices = grouped_expression_indices[profile_marker]
            vectors = np.vstack(
                [
                    vector_by_expression_id[int(raw_expressions[expression_index].get("id") or 0)]
                    for expression_index in expression_indices
                ]
            ).astype(np.float32)
            vectors = l2_normalize(vectors).astype(np.float32)
            previous_centers = previous_profile_cluster_centers.get(profile_marker)
            previous_cluster_count = int(previous_centers.shape[0]) if previous_centers is not None else 0
            cluster_count = self._choose_cluster_count(
                sample_count=vectors.shape[0],
                previous_cluster_count=previous_cluster_count,
            )
            labels = self._run_kmeans(vectors, cluster_count=cluster_count)
            cluster_centers = self._build_cluster_centers_from_labels(vectors, labels, cluster_count)

            for local_index, expression_index in enumerate(expression_indices):
                raw_expressions[expression_index]["vector_index"] = local_index
                raw_expressions[expression_index]["cluster_id"] = int(labels[local_index])

            vectors_key = f"vectors_{profile_index}"
            cluster_centers_key = f"cluster_centers_{profile_index}"
            profile_vectors[profile_marker] = vectors
            profile_cluster_centers[profile_marker] = cluster_centers
            first_expression = raw_expressions[expression_indices[0]]
            profile_metadata.append(
                {
                    "marker": profile_marker,
                    "profile_version": EMBEDDING_PROFILE_VERSION,
                    "embedding_model": normalize_text(first_expression.get("embedding_model")),
                    "embedding_dimension": int(first_expression.get("embedding_dimension") or vectors.shape[1]),
                    "expression_count": len(expression_indices),
                    "cluster_count": int(cluster_centers.shape[0]),
                    "vectors_key": vectors_key,
                    "cluster_centers_key": cluster_centers_key,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )

        return profile_vectors, profile_cluster_centers, profile_metadata

    @staticmethod
    def _write_index_files(
        *,
        index_path: Path,
        vectors_path: Path,
        payload: dict[str, Any],
        profile_vectors: Dict[str, np.ndarray],
        profile_cluster_centers: Dict[str, np.ndarray],
    ) -> None:
        """写回索引 JSON 与 NPZ。"""

        index_path.parent.mkdir(parents=True, exist_ok=True)
        vectors_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_vectors_path = vectors_path.with_name(f"{vectors_path.stem}.tmp{vectors_path.suffix}")
        arrays: dict[str, np.ndarray] = {}
        for raw_profile in payload.get("embedding_profiles") or []:
            if not isinstance(raw_profile, dict):
                continue
            marker = normalize_text(raw_profile.get("marker"))
            vectors_key = normalize_text(raw_profile.get("vectors_key"))
            cluster_centers_key = normalize_text(raw_profile.get("cluster_centers_key"))
            if not marker or not vectors_key or not cluster_centers_key:
                continue
            arrays[vectors_key] = profile_vectors[marker].astype(np.float32)
            arrays[cluster_centers_key] = profile_cluster_centers[marker].astype(np.float32)
        if not arrays:
            raise ValueError("表达向量索引没有可写入的 embedding profile 数组")
        np.savez_compressed(temporary_vectors_path, **arrays)
        temporary_vectors_path.replace(vectors_path)
        _atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2))

    async def upsert_expressions_and_recluster(
        self,
        *,
        index_path: str,
        expressions: Sequence[ExpressionVectorIndexUpsertItem],
    ) -> None:
        """批量写入学习到的表达向量，并在批次结束后重聚类。"""

        normalized_items: List[ExpressionVectorIndexUpsertItem] = []
        item_positions: Dict[int, int] = {}
        for expression in expressions:
            expression_id = int(expression.id)
            normalized_situation = normalize_text(expression.situation)
            normalized_style = normalize_text(expression.style)
            if expression_id <= 0 or not normalized_situation or not normalized_style:
                raise ValueError(
                    f"表达向量索引写入参数无效: id={expression.id}, "
                    f"situation={expression.situation!r}, style={expression.style!r}"
                )
            normalized_item = ExpressionVectorIndexUpsertItem(
                id=expression_id,
                situation=normalized_situation,
                style=normalized_style,
                count=int(expression.count),
                session_id=normalize_text(expression.session_id) or None,
                checked=bool(expression.checked),
                modified_by=normalize_text(expression.modified_by),
            )
            if expression_id in item_positions:
                normalized_items[item_positions[expression_id]] = normalized_item
            else:
                item_positions[expression_id] = len(normalized_items)
                normalized_items.append(normalized_item)

        if not normalized_items:
            return

        resolved_index_path = resolve_project_path(index_path)
        embedding_session_id = normalize_text(normalized_items[0].session_id)
        current_profile = await self.get_current_embedding_profile(
            index_path=str(resolved_index_path),
            session_id=embedding_session_id,
        )

        from src.services.embedding_service import EmbeddingServiceClient

        embedding_client = EmbeddingServiceClient(
            task_name="embedding",
            request_type="expression.selection.index_batch",
            session_id=embedding_session_id,
        )
        embedding_results = await embedding_client.embed_texts(
            [
                expression_embedding_text(expression.situation, expression.style)
                for expression in normalized_items
            ],
            max_concurrent=min(3, len(normalized_items)),
            session_id=embedding_session_id,
        )
        next_vectors = np.array([result.embedding for result in embedding_results], dtype=np.float32)
        if next_vectors.ndim != 2 or next_vectors.shape[0] != len(normalized_items):
            raise ValueError(
                f"表达向量批量结果维度异常: vectors={next_vectors.shape}, expressions={len(normalized_items)}"
            )
        for embedding_result in embedding_results:
            self._validate_embedding_result_profile(
                embedding_result,
                current_profile,
                usage="表达向量索引批量写入",
            )
        next_vectors = l2_normalize(next_vectors).astype(np.float32)

        async with self._update_lock:
            current_fingerprints = self._load_current_expression_fingerprints()
            vectors_path = resolved_index_path.with_suffix(".npz")
            raw_expressions: List[dict[str, Any]] = []
            vector_by_expression_id: Dict[int, np.ndarray] = {}
            previous_profile_cluster_centers: Dict[str, np.ndarray] = {}
            payload: dict[str, Any]

            if resolved_index_path.exists():
                payload = json.loads(resolved_index_path.read_text(encoding="utf-8"))
                payload_version = int(payload.get("version") or 0)
                if payload_version < 1 or payload_version > VECTOR_INDEX_VERSION:
                    raise ValueError(f"表达向量索引版本不匹配: {payload.get('version')!r}")

                vectors_path = _resolve_vectors_path(resolved_index_path, payload)
                raw_expressions = [
                    dict(raw_expression)
                    for raw_expression in payload.get("expressions") or []
                    if isinstance(raw_expression, dict)
                ]

                raw_profiles = payload.get("embedding_profiles")
                if isinstance(raw_profiles, list) and raw_profiles:
                    profile_vectors: Dict[str, np.ndarray] = {}
                    for raw_profile in raw_profiles:
                        if not isinstance(raw_profile, dict):
                            continue
                        marker = normalize_text(raw_profile.get("marker")) or LEGACY_EMBEDDING_PROFILE_MARKER
                        vectors_key = normalize_text(raw_profile.get("vectors_key"))
                        cluster_centers_key = normalize_text(raw_profile.get("cluster_centers_key"))
                        if not vectors_key or not cluster_centers_key:
                            continue
                        profile_vectors[marker] = l2_normalize(_load_npz_array(vectors_path, vectors_key))
                        previous_profile_cluster_centers[marker] = l2_normalize(
                            _load_npz_array(vectors_path, cluster_centers_key)
                        )

                    for raw_expression in raw_expressions:
                        expression_id = int(raw_expression.get("id") or 0)
                        if not self._is_current_index_expression(raw_expression, current_fingerprints):
                            continue
                        marker = (
                            normalize_text(raw_expression.get("embedding_profile_marker"))
                            or LEGACY_EMBEDDING_PROFILE_MARKER
                        )
                        vector_index = int(raw_expression.get("vector_index") or 0)
                        vectors = profile_vectors.get(marker)
                        if expression_id <= 0 or vectors is None:
                            continue
                        if vector_index < 0 or vector_index >= vectors.shape[0]:
                            raise ValueError(
                                f"表达向量索引 vector_index 越界: id={expression_id}, "
                                f"marker={marker[:12]}, vector_index={vector_index}"
                            )
                        raw_expression["embedding_profile_marker"] = marker
                        vector_by_expression_id[expression_id] = vectors[vector_index].astype(np.float32)
                else:
                    legacy_marker = (
                        normalize_text(payload.get("embedding_profile_marker")) or LEGACY_EMBEDDING_PROFILE_MARKER
                    )
                    vectors = l2_normalize(_load_npz_array(vectors_path, "vectors"))
                    previous_profile_cluster_centers[legacy_marker] = l2_normalize(
                        _load_npz_array(vectors_path, "cluster_centers")
                    )
                    if vectors.shape[0] != len(raw_expressions):
                        raise ValueError(
                            f"表达向量索引数量不一致: vectors={vectors.shape[0]}, expressions={len(raw_expressions)}"
                        )
                    for expression_index, raw_expression in enumerate(raw_expressions):
                        expression_id = int(raw_expression.get("id") or 0)
                        if not self._is_current_index_expression(raw_expression, current_fingerprints):
                            continue
                        raw_expression["embedding_profile_marker"] = legacy_marker
                        raw_expression["embedding_model"] = normalize_text(
                            raw_expression.get("embedding_model") or payload.get("embedding_model")
                        )
                        raw_expression["embedding_dimension"] = int(
                            raw_expression.get("embedding_dimension") or vectors.shape[1]
                        )
                        raw_expression["vector_index"] = expression_index
                        vector_by_expression_id[expression_id] = vectors[expression_index].astype(np.float32)
            else:
                from src.common.database.database import DATABASE_URL

                payload = {
                    "version": VECTOR_INDEX_VERSION,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "database_url": DATABASE_URL,
                    "args": {"source": "incremental_learning"},
                }

            raw_expressions = [
                raw_expression
                for raw_expression in raw_expressions
                if self._is_current_index_expression(raw_expression, current_fingerprints)
            ]
            expression_positions = {
                int(raw_expression.get("id") or 0): index
                for index, raw_expression in enumerate(raw_expressions)
            }
            vector_by_expression_id = {
                expression_id: vector
                for expression_id, vector in vector_by_expression_id.items()
                if expression_id in expression_positions
            }
            for item_index, expression in enumerate(normalized_items):
                next_expression = self._build_index_expression_item(
                    expression_id=expression.id,
                    situation=expression.situation,
                    style=expression.style,
                    count=expression.count,
                    session_id=expression.session_id,
                    checked=expression.checked,
                    modified_by=expression.modified_by,
                    embedding_profile_marker=current_profile.marker,
                    embedding_model=current_profile.model_name,
                    embedding_dimension=current_profile.dimension,
                    vector_index=0,
                    cluster_id=0,
                )
                vector_by_expression_id[expression.id] = next_vectors[item_index].astype(np.float32)
                if expression.id in expression_positions:
                    expression_index = expression_positions[expression.id]
                    raw_expressions[expression_index] = next_expression
                    continue
                expression_positions[expression.id] = len(raw_expressions)
                raw_expressions.append(next_expression)

            profile_vectors, profile_cluster_centers, profile_metadata = self._rebuild_profile_arrays(
                raw_expressions=raw_expressions,
                vector_by_expression_id=vector_by_expression_id,
                previous_profile_cluster_centers=previous_profile_cluster_centers,
            )

            now_text = datetime.now().isoformat(timespec="seconds")
            payload["version"] = VECTOR_INDEX_VERSION
            payload.setdefault("generated_at", now_text)
            payload["updated_at"] = now_text
            payload["embedding_model"] = current_profile.model_name
            payload["embedding_profile_marker"] = current_profile.marker
            payload["embedding_profile_version"] = EMBEDDING_PROFILE_VERSION
            payload["embedding_dimension"] = int(current_profile.dimension)
            payload["embedding_profile"] = _serialize_embedding_profile(current_profile)
            payload["embedding_profiles"] = profile_metadata
            payload["sample_count"] = len(raw_expressions)
            payload["clusters"] = self._build_cluster_summaries(raw_expressions)
            payload["vectors_file"] = vectors_path.name
            payload["expressions"] = raw_expressions

            self._write_index_files(
                index_path=resolved_index_path,
                vectors_path=vectors_path,
                payload=payload,
                profile_vectors=profile_vectors,
                profile_cluster_centers=profile_cluster_centers,
            )
            self._snapshot = None
            logger.info(
                f"表达向量索引批量同步并重聚类完成: path={resolved_index_path} "
                f"batch_count={len(normalized_items)} total_count={len(raw_expressions)} "
                f"profile={current_profile.marker[:12]}"
            )

    def ensure_history_backfill_task(
        self,
        *,
        index_path: str,
    ) -> None:
        """确保历史表达向量补建后台任务正在运行。"""

        if self._history_backfill_task is not None and not self._history_backfill_task.done():
            return

        now = time.monotonic()
        if now - self._history_backfill_last_empty_at < HISTORY_BACKFILL_EMPTY_SCAN_INTERVAL_SECONDS:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("表达向量历史补建未启动：当前没有运行中的事件循环")
            return

        task = loop.create_task(
            self._run_history_backfill_loop(
                index_path=index_path,
            )
        )
        self._history_backfill_task = task
        task.add_done_callback(self._handle_history_backfill_done)
        logger.info(
            f"表达向量历史补建任务已启动: index={resolve_project_path(index_path)} "
            f"batch_size={HISTORY_BACKFILL_BATCH_SIZE}"
        )

    def _handle_history_backfill_done(self, task: asyncio.Task[None]) -> None:
        """清理历史表达向量补建任务状态。"""

        if self._history_backfill_task is task:
            self._history_backfill_task = None
        if task.cancelled():
            logger.debug("表达向量历史补建任务已取消")
            return
        try:
            task.result()
        except Exception:
            logger.exception("表达向量历史补建任务异常退出")

    @staticmethod
    def _calculate_history_backfill_interval(
        *,
        elapsed_seconds: float,
        min_interval_seconds: float,
        max_interval_seconds: float,
        interval_speed_ratio: float,
    ) -> float:
        """根据上一批耗时计算下一批间隔。"""

        min_interval = max(0.0, float(min_interval_seconds))
        max_interval = max(min_interval, float(max_interval_seconds))
        ratio = max(0.0, float(interval_speed_ratio))
        dynamic_interval = max(min_interval, float(elapsed_seconds) * ratio)
        return min(max_interval, dynamic_interval)

    async def _run_history_backfill_loop(
        self,
        *,
        index_path: str,
    ) -> None:
        """分批补建历史表达向量；每批结束后按耗时自适应等待。"""

        resolved_index_path = resolve_project_path(index_path)
        effective_batch_size = HISTORY_BACKFILL_BATCH_SIZE
        while True:
            from src.config.config import global_config

            if global_config.expression.expression_selection_mode not in {"vector", "vector_intent"}:
                logger.info("表达向量历史补建已停止：当前表达选择模式不是向量模式")
                return

            batch_started_at = time.monotonic()
            current_profile = await self.get_current_embedding_profile(index_path=str(resolved_index_path))
            items = await asyncio.to_thread(
                self._load_history_backfill_items,
                index_path=resolved_index_path,
                profile=current_profile,
                batch_size=effective_batch_size,
            )
            if not items:
                self._history_backfill_last_empty_at = time.monotonic()
                logger.info(
                    f"表达向量历史补建已完成: index={resolved_index_path} "
                    f"profile={current_profile.marker[:12]}"
                )
                return

            await self.upsert_expressions_and_recluster(
                index_path=str(resolved_index_path),
                expressions=items,
            )
            elapsed_seconds = time.monotonic() - batch_started_at
            interval_seconds = self._calculate_history_backfill_interval(
                elapsed_seconds=elapsed_seconds,
                min_interval_seconds=HISTORY_BACKFILL_MIN_INTERVAL_SECONDS,
                max_interval_seconds=HISTORY_BACKFILL_MAX_INTERVAL_SECONDS,
                interval_speed_ratio=HISTORY_BACKFILL_INTERVAL_SPEED_RATIO,
            )
            logger.info(
                f"表达向量历史补建批次完成: batch_count={len(items)} "
                f"耗时={elapsed_seconds:.2f}s 下批间隔={interval_seconds:.2f}s"
            )
            if len(items) < effective_batch_size:
                self._history_backfill_last_empty_at = time.monotonic()
                logger.info(
                    f"表达向量历史补建已追平: index={resolved_index_path} "
                    f"profile={current_profile.marker[:12]}"
                )
                return
            if interval_seconds > 0:
                await asyncio.sleep(interval_seconds)

    async def select_candidates(
        self,
        *,
        index_path: str,
        session_id: str,
        query_text: str,
        scoped_candidates: Sequence[dict[str, Any]],
        candidate_pool_size: int,
        cluster_pool_size: int,
    ) -> List[dict[str, Any]]:
        """从当前聊天流候选中召回最贴近 query 的表达方式。"""

        normalized_query = normalize_text(query_text)
        if not normalized_query:
            logger.info("表达向量召回已跳过：query 为空")
            return []

        snapshot = self._load_snapshot(resolve_project_path(index_path))
        if snapshot is None:
            return []

        current_profile = await self.get_current_embedding_profile(
            index_path=index_path,
            session_id=session_id,
        )
        profile_vectors = snapshot.profile_vectors.get(current_profile.marker)
        profile_cluster_centers = snapshot.profile_cluster_centers.get(current_profile.marker)
        if profile_vectors is None or profile_cluster_centers is None:
            logger.info(
                f"表达向量召回已跳过：索引缺少当前 embedding profile "
                f"marker={current_profile.marker[:12]} model={current_profile.model_name}"
            )
            return []

        indexed_candidates = self._filter_indexed_expressions(snapshot, scoped_candidates, current_profile)
        if len(indexed_candidates) < 10:
            logger.info(
                f"表达向量召回已跳过：当前 profile 范围内可用索引候选不足 "
                f"count={len(indexed_candidates)} marker={current_profile.marker[:12]}"
            )
            return []

        from src.services.embedding_service import EmbeddingServiceClient

        embedding_client = EmbeddingServiceClient(
            task_name="embedding",
            request_type="expression.selection.vector_query",
            session_id=session_id,
        )
        query_result = await embedding_client.embed_text(normalized_query, session_id=session_id)
        self._validate_embedding_result_profile(query_result, current_profile, usage="表达向量 query")
        query_vector = np.array(query_result.embedding, dtype=np.float32).reshape(1, -1)
        query_vector = l2_normalize(query_vector)[0]
        if query_vector.shape[0] != profile_vectors.shape[1]:
            raise ValueError(
                f"表达向量 query 维度与当前 profile 索引不一致: "
                f"query={query_vector.shape[0]}, index={profile_vectors.shape[1]}"
            )

        cluster_scores = profile_cluster_centers @ query_vector
        ordered_cluster_ids = [int(index) for index in np.argsort(cluster_scores)[::-1]]
        effective_cluster_pool = max(1, int(cluster_pool_size))
        effective_limit = max(1, min(VECTOR_CANDIDATE_HARD_LIMIT, int(candidate_pool_size)))
        indexed_by_cluster: Dict[int, List[IndexedExpression]] = {}
        for candidate in indexed_candidates:
            indexed_by_cluster.setdefault(candidate.cluster_id, []).append(candidate)

        selected_cluster_ids: List[int] = []
        pool_candidates: List[IndexedExpression] = []
        for cluster_id in ordered_cluster_ids:
            cluster_members = indexed_by_cluster.get(cluster_id)
            if not cluster_members:
                continue
            selected_cluster_ids.append(cluster_id)
            pool_candidates.extend(cluster_members)
            if len(selected_cluster_ids) >= effective_cluster_pool and len(pool_candidates) >= effective_limit:
                break

        if not pool_candidates:
            return []

        query_tokens = lexical_tokens(normalized_query)
        scored_candidates: List[dict[str, Any]] = []
        total_weight = VECTOR_ITEM_WEIGHT + VECTOR_CLUSTER_WEIGHT + VECTOR_LEXICAL_WEIGHT
        for candidate in pool_candidates:
            item_similarity = float(profile_vectors[candidate.index] @ query_vector)
            cluster_similarity = float(cluster_scores[candidate.cluster_id])
            lexical_similarity = lexical_overlap_score(query_tokens, candidate)
            score = (
                item_similarity * VECTOR_ITEM_WEIGHT
                + cluster_similarity * VECTOR_CLUSTER_WEIGHT
                + lexical_similarity * VECTOR_LEXICAL_WEIGHT
            ) / total_weight
            scored_candidates.append(
                {
                    "id": candidate.id,
                    "situation": candidate.situation,
                    "style": candidate.style,
                    "count": candidate.count,
                    "selector_score": round(float(score), 4),
                    "item_similarity": round(item_similarity, 4),
                    "cluster_similarity": round(cluster_similarity, 4),
                    "lexical_similarity": round(lexical_similarity, 4),
                    "cluster_id": candidate.cluster_id,
                    "vector_index": candidate.index,
                    "score": float(score),
                }
            )

        selected_matches = self._select_by_mmr(scored_candidates, profile_vectors, limit=effective_limit)
        selected_matches.sort(key=lambda item: float(item["score"]), reverse=True)
        for match in selected_matches:
            match.pop("score", None)
            match.pop("vector_index", None)
        return selected_matches


expression_vector_index = ExpressionVectorIndex()

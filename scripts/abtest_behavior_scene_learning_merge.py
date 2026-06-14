from argparse import ArgumentParser, Namespace
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from random import Random
from time import perf_counter
from typing import Any, Sequence

import asyncio
import hashlib
import json
import math
import sys

from json_repair import repair_json
from sqlalchemy import func
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.offline_behavior_learning import _chat_display_name  # noqa: E402
from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import ChatSession, Messages  # noqa: E402
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import BehaviorScenarioProfile, parse_behavior_scenario_segments_response  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    MAX_TAG_CLUSTER_MEMBERS,
    MIN_TAG_CLUSTER_MERGE_OVERLAP,
    SCENE_CLUSTER_REUSE_THRESHOLD,
    TAG_KIND_WEIGHTS,
    _cluster_distribution_overlap,
    _distribution_to_mapping,
    _load_tag_cluster_lookup,
    _normalize_tag_kind,
    _normalize_tag_value,
    _tag_cluster_values,
    build_scene_cluster_distribution,
    format_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.embedding_service import EmbeddingServiceClient  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_scene_learning_merge_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_scene_learning_merge_abtest.json"
DEFAULT_CACHE = "data/analysis/behavior_scene_embedding_match_abtest_cache.json"


@dataclass(frozen=True)
class SourceWindow:
    session_id: str
    display_name: str
    messages: list[SessionMessage]


@dataclass(frozen=True)
class SceneSample:
    index: int
    session_id: str
    display_name: str
    window_hash: str
    context_preview: str
    profile: BehaviorScenarioProfile
    distribution: list[dict[str, Any]]
    profile_text: str
    profile_embedding: list[float]
    analysis_ms: float
    embedding_ms: float
    profile_cached: bool
    embedding_cached: bool


@dataclass
class SyntheticCluster:
    cluster_id: int
    session_id: str
    display_name: str
    distribution: list[dict[str, Any]]
    source_indices: list[int] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    centroid_embedding: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Assignment:
    sample_index: int
    cluster_id: int
    action: str
    tag_overlap: float
    embedding_score: float


@dataclass(frozen=True)
class BuildResult:
    strategy: str
    clusters: list[SyntheticCluster]
    assignments: list[Assignment]
    sample_distributions: dict[int, list[dict[str, Any]]]
    elapsed_ms: float


@dataclass
class InMemoryTagClusterState:
    lookup: dict[tuple[str, str], str]
    members_by_key: dict[tuple[str, str], list[str]]
    next_index: int = 1

    @classmethod
    def from_lookup(cls, tag_lookup: dict[tuple[str, str], str]) -> "InMemoryTagClusterState":
        members_by_key: dict[tuple[str, str], list[str]] = {}
        for (tag_kind, tag), cluster_key in tag_lookup.items():
            if not tag_kind or not tag or not cluster_key:
                continue
            members_by_key.setdefault((tag_kind, cluster_key), []).append(tag)
        return cls(lookup=dict(tag_lookup), members_by_key=members_by_key)

    def upsert_profile(self, profile: BehaviorScenarioProfile) -> None:
        for cluster in profile.tag_clusters:
            tag_kind = _normalize_tag_kind(cluster.kind)
            if tag_kind not in TAG_KIND_WEIGHTS:
                continue
            values = _tag_cluster_values(cluster)
            normalized_tags = [_normalize_tag_value(value) for value in values if _normalize_tag_value(value)]
            normalized_tags = list(dict.fromkeys(normalized_tags))
            if not normalized_tags:
                continue

            candidate_keys = {
                self.lookup[(tag_kind, tag)]
                for tag in normalized_tags
                if (tag_kind, tag) in self.lookup and self.lookup[(tag_kind, tag)]
            }
            chosen_key = self._choose_merge_key(tag_kind=tag_kind, tags=normalized_tags, candidate_keys=candidate_keys)
            if not chosen_key:
                chosen_key = f"tc_abtest_{self.next_index:04d}"
                self.next_index += 1

            members = self.members_by_key.setdefault((tag_kind, chosen_key), [])
            for tag in normalized_tags:
                existing_key = self.lookup.get((tag_kind, tag), "")
                if existing_key and existing_key != chosen_key:
                    continue
                if tag not in members and len(members) < MAX_TAG_CLUSTER_MEMBERS:
                    members.append(tag)
                self.lookup[(tag_kind, tag)] = chosen_key

    def build_scene_distribution(self, profile: BehaviorScenarioProfile) -> list[dict[str, Any]]:
        self.upsert_profile(profile)
        return [dict(item) for item in build_scene_cluster_distribution(profile, tag_lookup=self.lookup)]

    def _choose_merge_key(self, *, tag_kind: str, tags: Sequence[str], candidate_keys: set[str]) -> str:
        best_key = ""
        best_score = -1
        incoming_tags = set(tags)
        for cluster_key in candidate_keys:
            members = set(self.members_by_key.get((tag_kind, cluster_key), []))
            overlap_count = len(incoming_tags & members)
            if overlap_count < MIN_TAG_CLUSTER_MERGE_OVERLAP:
                continue
            if overlap_count > best_score:
                best_key = cluster_key
                best_score = overlap_count
        return best_key


def _round_ms(value: float) -> float:
    return round(float(value) * 1000.0, 2)


def _split_values(raw_values: Sequence[str]) -> list[str]:
    values: list[str] = []
    for raw_value in raw_values:
        for item in str(raw_value or "").replace("，", ",").split(","):
            value = item.strip()
            if value and value not in values:
                values.append(value)
    return values


def _load_messages_by_session(
    session: Session,
    *,
    session_id: str,
    min_text_length: int,
    limit: int,
) -> list[SessionMessage]:
    statement = (
        select(Messages)
        .where(Messages.session_id == session_id)
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .order_by(Messages.timestamp.asc())  # type: ignore[attr-defined]
    )
    if limit > 0:
        statement = statement.limit(limit)

    messages: list[SessionMessage] = []
    for record in session.exec(statement).all():
        text = " ".join((record.processed_plain_text or "").split()).strip()
        if len(text) < min_text_length:
            continue
        messages.append(SessionMessage.from_db_instance(record))
    return messages


def _discover_session_ids(session: Session, *, min_messages: int) -> list[str]:
    statement = (
        select(Messages.session_id, func.count(Messages.id))
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .group_by(Messages.session_id)
        .having(func.count(Messages.id) >= min_messages)
        .order_by(func.count(Messages.id).desc(), Messages.session_id.asc())  # type: ignore[attr-defined]
    )
    return [str(row[0]) for row in session.exec(statement).all() if str(row[0] or "").strip()]


def _select_windows(args: Namespace) -> list[SourceWindow]:
    randomizer = Random(args.seed)
    with get_db_session(auto_commit=False) as session:
        session_ids = _split_values([*args.session_id, *args.chat_id])
        if not session_ids:
            session_ids = _discover_session_ids(session, min_messages=args.window_size)
        candidates: list[SourceWindow] = []
        for session_id in session_ids:
            chat_session = session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
            if chat_session is None:
                continue
            messages = _load_messages_by_session(
                session,
                session_id=session_id,
                min_text_length=args.min_text_length,
                limit=args.limit,
            )
            if len(messages) < args.window_size:
                continue
            display_name = _chat_display_name(chat_session)
            for start in range(0, len(messages) - args.window_size + 1, max(1, args.step)):
                candidates.append(
                    SourceWindow(
                        session_id=session_id,
                        display_name=display_name,
                        messages=messages[start : start + args.window_size],
                    )
                )
    randomizer.shuffle(candidates)
    return candidates[: args.samples]


def _window_hash(messages: Sequence[SessionMessage]) -> str:
    payload = "\n".join(str(message.message_id or "") for message in messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _message_context_text(messages: Sequence[SessionMessage], *, max_message_length: int = 160) -> str:
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        text = " ".join(str(message.processed_plain_text or "").split()).strip()
        if len(text) > max_message_length:
            text = text[:max_message_length].rstrip() + "..."
        lines.append(
            "\n".join(
                [
                    f"[source_id:{index}]",
                    f"[name:{speaker_name}]",
                    f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                    f"[content] {text or '[空消息]'}",
                ]
            )
        )
    return "\n\n".join(lines)


def _context_preview(messages: Sequence[SessionMessage], *, max_lines: int = 6, max_message_length: int = 90) -> str:
    lines: list[str] = []
    for message in messages[:max_lines]:
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        text = " ".join(str(message.processed_plain_text or "").split()).strip()
        if len(text) > max_message_length:
            text = text[:max_message_length].rstrip() + "..."
        lines.append(f"{speaker_name}: {text}")
    return "\n".join(lines)


def _request_messages_for_scene(messages: Sequence[SessionMessage]) -> list[Any]:
    prompt = load_prompt("behavior_scene_analyze", bot_name=global_config.bot.nickname)
    request_messages = [MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build()]
    for index, message in enumerate(messages, start=1):
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        request_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content(
                "\n".join(
                    [
                        f"[source_id:{index}]",
                        "[speaker:USER]",
                        f"[name:{speaker_name}]",
                        f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                        "[content]",
                        str(message.processed_plain_text or "[空消息]"),
                    ]
                )
            )
            .build()
        )
    request_messages.append(MessageBuilder().set_role(RoleType.User).add_text_content("请根据以上聊天消息输出 JSON。").build())
    return request_messages


def _parse_first_profile(raw_response: str) -> BehaviorScenarioProfile | None:
    segments = parse_behavior_scenario_segments_response(raw_response)
    if not segments:
        return None
    profile = segments[0].profile
    return profile if profile.tag_clusters else None


def _profile_payload(profile: BehaviorScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "tag_clusters": profile.domain_prompt_payloads(),
        "need": profile.need_prompt_payload(),
        "other_traits": profile.other_traits_prompt_payloads(),
        "confidence": profile.confidence,
        "tag_key": profile.tag_cluster_text(),
    }


def _profile_text(profile: BehaviorScenarioProfile) -> str:
    return json.dumps(_profile_payload(profile), ensure_ascii=False, sort_keys=True)


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        parsed = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


async def _analyze_profile(
    window: SourceWindow,
    *,
    client: LLMServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
    temperature: float,
    cache_only: bool,
) -> tuple[BehaviorScenarioProfile | None, bool, float]:
    cache_key = f"scene_profile:{_window_hash(window.messages)}:t{temperature}"
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict) and isinstance(cached_item.get("raw_response"), str)
    start = perf_counter()
    if cached:
        raw_response = str(cached_item.get("raw_response") or "")
    elif cache_only:
        return None, False, _round_ms(perf_counter() - start)
    else:
        result = await client.generate_response_with_messages(
            lambda _client: _request_messages_for_scene(window.messages),
            options=LLMGenerationOptions(temperature=temperature),
        )
        raw_response = result.response or ""
        cache[cache_key] = {
            "raw_response": raw_response,
            "session_id": window.session_id,
            "message_ids": [str(message.message_id or "") for message in window.messages],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save_cache(cache_path, cache)
    return _parse_first_profile(raw_response), cached, _round_ms(perf_counter() - start)


def _embedding_cache_key(text: str) -> str:
    return "embedding:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _get_embedding(
    text: str,
    *,
    client: EmbeddingServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
    cache_only: bool = False,
) -> tuple[list[float], bool, float]:
    cache_key = _embedding_cache_key(text)
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict) and isinstance(cached_item.get("embedding"), list)
    start = perf_counter()
    if cached:
        embedding = [float(value) for value in cached_item["embedding"]]
    elif cache_only:
        return [], False, _round_ms(perf_counter() - start)
    else:
        result = await client.embed_text(text)
        embedding = [float(value) for value in result.embedding]
        cache[cache_key] = {"embedding": embedding, "model_name": result.model_name}
        _save_cache(cache_path, cache)
    return embedding, cached, _round_ms(perf_counter() - start)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _average_embeddings(left: Sequence[float], right: Sequence[float], *, existing_weight: int) -> list[float]:
    if not left:
        return [float(value) for value in right]
    if not right or len(left) != len(right):
        return [float(value) for value in left]
    return [
        (float(a) * float(existing_weight) + float(b)) / (float(existing_weight) + 1.0)
        for a, b in zip(left, right, strict=True)
    ]


def _merge_distributions(
    existing_distribution: Sequence[dict[str, Any]],
    new_distribution: Sequence[dict[str, Any]],
    *,
    existing_weight: int,
    tag_lookup: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    existing_probs = _distribution_to_mapping(existing_distribution, tag_lookup=tag_lookup)
    new_probs = _distribution_to_mapping(new_distribution, tag_lookup=tag_lookup)
    if not existing_probs:
        return [dict(item) for item in new_distribution]
    merged_probs: dict[str, float] = {}
    for tag in set(existing_probs) | set(new_probs):
        merged_probs[tag] = (
            existing_probs.get(tag, 0.0) * float(existing_weight)
            + new_probs.get(tag, 0.0)
        ) / (float(existing_weight) + 1.0)
    total_probability = sum(max(probability, 0.0) for probability in merged_probs.values())
    if total_probability <= 0.0:
        return []
    return [
        {"tag": tag, "probability": round(max(probability, 0.0) / total_probability, 6)}
        for tag, probability in sorted(merged_probs.items())
        if probability > 0.0
    ]


def _same_scope_clusters(clusters: Sequence[SyntheticCluster], sample: SceneSample) -> list[SyntheticCluster]:
    return [cluster for cluster in clusters if cluster.session_id == sample.session_id]


def _best_tag_match(
    clusters: Sequence[SyntheticCluster],
    sample: SceneSample,
    *,
    distribution: Sequence[dict[str, Any]],
    tag_lookup: dict[tuple[str, str], str],
) -> tuple[SyntheticCluster | None, float]:
    best_cluster: SyntheticCluster | None = None
    best_overlap = 0.0
    for cluster in _same_scope_clusters(clusters, sample):
        overlap = _cluster_distribution_overlap(cluster.distribution, distribution, tag_lookup=tag_lookup)
        if overlap > best_overlap:
            best_cluster = cluster
            best_overlap = overlap
    return best_cluster, best_overlap


def _best_embedding_match(
    clusters: Sequence[SyntheticCluster],
    sample: SceneSample,
) -> tuple[SyntheticCluster | None, float]:
    best_cluster: SyntheticCluster | None = None
    best_score = 0.0
    for cluster in _same_scope_clusters(clusters, sample):
        score = _cosine(sample.profile_embedding, cluster.centroid_embedding)
        if score > best_score:
            best_cluster = cluster
            best_score = score
    return best_cluster, best_score


def _create_cluster(
    sample: SceneSample,
    *,
    cluster_id: int,
    distribution: Sequence[dict[str, Any]],
) -> SyntheticCluster:
    return SyntheticCluster(
        cluster_id=cluster_id,
        session_id=sample.session_id,
        display_name=sample.display_name,
        distribution=[dict(item) for item in distribution],
        source_indices=[sample.index],
        summaries=[sample.profile.summary],
        centroid_embedding=[float(value) for value in sample.profile_embedding],
    )


def _merge_sample_into_cluster(
    cluster: SyntheticCluster,
    sample: SceneSample,
    *,
    distribution: Sequence[dict[str, Any]],
    tag_lookup: dict[tuple[str, str], str],
) -> None:
    existing_weight = max(len(cluster.source_indices), 1)
    cluster.distribution = _merge_distributions(
        cluster.distribution,
        distribution,
        existing_weight=existing_weight,
        tag_lookup=tag_lookup,
    )
    cluster.centroid_embedding = _average_embeddings(
        cluster.centroid_embedding,
        sample.profile_embedding,
        existing_weight=existing_weight,
    )
    cluster.source_indices.append(sample.index)
    cluster.summaries.append(sample.profile.summary)


def _build_original_library(
    samples: Sequence[SceneSample],
    *,
    tag_lookup: dict[tuple[str, str], str],
) -> BuildResult:
    start = perf_counter()
    tag_state = InMemoryTagClusterState.from_lookup(tag_lookup)
    clusters: list[SyntheticCluster] = []
    assignments: list[Assignment] = []
    sample_distributions: dict[int, list[dict[str, Any]]] = {}
    next_cluster_id = 1
    for sample in samples:
        distribution = tag_state.build_scene_distribution(sample.profile)
        sample_distributions[sample.index] = distribution
        best_cluster, best_overlap = _best_tag_match(
            clusters,
            sample,
            distribution=distribution,
            tag_lookup=tag_state.lookup,
        )
        if best_cluster is not None and best_overlap >= SCENE_CLUSTER_REUSE_THRESHOLD:
            _merge_sample_into_cluster(
                best_cluster,
                sample,
                distribution=distribution,
                tag_lookup=tag_state.lookup,
            )
            assignments.append(
                Assignment(
                    sample_index=sample.index,
                    cluster_id=best_cluster.cluster_id,
                    action="tag_merge",
                    tag_overlap=round(best_overlap, 4),
                    embedding_score=0.0,
                )
            )
        else:
            cluster = _create_cluster(sample, cluster_id=next_cluster_id, distribution=distribution)
            next_cluster_id += 1
            clusters.append(cluster)
            assignments.append(
                Assignment(
                    sample_index=sample.index,
                    cluster_id=cluster.cluster_id,
                    action="create",
                    tag_overlap=round(best_overlap, 4),
                    embedding_score=0.0,
                )
            )
    return BuildResult(
        "original_tag_only",
        clusters,
        assignments,
        sample_distributions,
        _round_ms(perf_counter() - start),
    )


def _build_hybrid_library(
    samples: Sequence[SceneSample],
    *,
    tag_lookup: dict[tuple[str, str], str],
    embedding_threshold: float,
) -> BuildResult:
    start = perf_counter()
    tag_state = InMemoryTagClusterState.from_lookup(tag_lookup)
    clusters: list[SyntheticCluster] = []
    assignments: list[Assignment] = []
    sample_distributions: dict[int, list[dict[str, Any]]] = {}
    next_cluster_id = 1
    for sample in samples:
        distribution = tag_state.build_scene_distribution(sample.profile)
        sample_distributions[sample.index] = distribution
        best_cluster, best_overlap = _best_tag_match(
            clusters,
            sample,
            distribution=distribution,
            tag_lookup=tag_state.lookup,
        )
        if best_cluster is not None and best_overlap >= SCENE_CLUSTER_REUSE_THRESHOLD:
            _merge_sample_into_cluster(
                best_cluster,
                sample,
                distribution=distribution,
                tag_lookup=tag_state.lookup,
            )
            assignments.append(
                Assignment(
                    sample_index=sample.index,
                    cluster_id=best_cluster.cluster_id,
                    action="tag_merge",
                    tag_overlap=round(best_overlap, 4),
                    embedding_score=0.0,
                )
            )
            continue

        embedding_cluster, embedding_score = _best_embedding_match(clusters, sample)
        if embedding_cluster is not None and embedding_score >= embedding_threshold:
            _merge_sample_into_cluster(
                embedding_cluster,
                sample,
                distribution=distribution,
                tag_lookup=tag_state.lookup,
            )
            assignments.append(
                Assignment(
                    sample_index=sample.index,
                    cluster_id=embedding_cluster.cluster_id,
                    action="embedding_merge",
                    tag_overlap=round(best_overlap, 4),
                    embedding_score=round(embedding_score, 4),
                )
            )
            continue

        cluster = _create_cluster(sample, cluster_id=next_cluster_id, distribution=distribution)
        next_cluster_id += 1
        clusters.append(cluster)
        assignments.append(
            Assignment(
                sample_index=sample.index,
                cluster_id=cluster.cluster_id,
                action="create",
                tag_overlap=round(best_overlap, 4),
                embedding_score=round(embedding_score, 4),
            )
        )
    return BuildResult(
        "tag_then_embedding",
        clusters,
        assignments,
        sample_distributions,
        _round_ms(perf_counter() - start),
    )


def _extract_json_object(raw_response: str) -> dict[str, Any]:
    content = (raw_response or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(repair_json(content))
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _cluster_brief(cluster: SyntheticCluster, samples_by_index: dict[int, SceneSample]) -> dict[str, Any]:
    member_samples = [samples_by_index[index] for index in cluster.source_indices if index in samples_by_index]
    return {
        "cluster_id": cluster.cluster_id,
        "session_id": cluster.session_id,
        "display_name": cluster.display_name,
        "source_count": len(cluster.source_indices),
        "distribution": cluster.distribution,
        "distribution_text": format_scene_cluster_distribution(cluster.distribution),
        "members": [
            {
                "sample_index": sample.index,
                "summary": sample.profile.summary,
                "context_preview": sample.context_preview,
            }
            for sample in member_samples
        ],
    }


async def _judge_cluster_coherence(
    *,
    strategy: str,
    cluster: SyntheticCluster,
    samples_by_index: dict[int, SceneSample],
    client: LLMServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
) -> tuple[dict[str, Any], bool, float]:
    if len(cluster.source_indices) <= 1:
        return {"score": 5.0, "reason": "单样本簇无需判断"}, True, 0.0

    payload = {
        "strategy": strategy,
        "cluster": _cluster_brief(cluster, samples_by_index),
    }
    cache_key = "learning_merge_cluster_judge:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached_item = cache.get(cache_key)
    start = perf_counter()
    if isinstance(cached_item, dict):
        return cached_item, True, _round_ms(perf_counter() - start)

    prompt = (
        "你是行为学习场景簇质检器。请判断同一个簇里的样本是否真的属于同一类可合并场景。\n"
        "评分标准：5=高度一致，可直接合并；4=同一大场景，略有子主题差异；3=有共同主题但混入明显不同意图；"
        "2=只有宽泛相关；1=基本不该合并；0=完全错误。\n"
        "只输出 JSON：{\"score\": 0-5, \"reason\": \"简短原因\", \"bad_members\": [样本编号]}。"
    )
    messages = [
        MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build(),
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content(json.dumps(payload, ensure_ascii=False, indent=2))
        .build(),
    ]
    result = await client.generate_response_with_messages(
        lambda _client: messages,
        options=LLMGenerationOptions(temperature=0.0),
    )
    parsed = _extract_json_object(result.response or "")
    try:
        score = float(parsed.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    judge_payload = {
        "score": round(max(0.0, min(5.0, score)), 2),
        "reason": str(parsed.get("reason") or "").strip()[:500],
        "bad_members": parsed.get("bad_members") if isinstance(parsed.get("bad_members"), list) else [],
        "raw_response": result.response or "",
    }
    cache[cache_key] = judge_payload
    _save_cache(cache_path, cache)
    return judge_payload, False, _round_ms(perf_counter() - start)


async def _collect_samples(
    *,
    windows: Sequence[SourceWindow],
    args: Namespace,
    cache: dict[str, Any],
    cache_path: Path,
    scene_client: LLMServiceClient,
    embedding_client: EmbeddingServiceClient,
    tag_lookup: dict[tuple[str, str], str],
) -> list[SceneSample]:
    samples: list[SceneSample] = []
    for index, window in enumerate(windows, start=1):
        profile, profile_cached, analysis_ms = await _analyze_profile(
            window,
            client=scene_client,
            cache=cache,
            cache_path=cache_path,
            temperature=args.temperature,
            cache_only=args.cache_only,
        )
        if profile is None:
            if args.cache_only:
                print(f"[skip] {window.display_name} scene_profile_cache_missing")
            continue
        distribution = build_scene_cluster_distribution(profile, tag_lookup=tag_lookup)
        if not distribution:
            continue
        profile_text = _profile_text(profile)
        profile_embedding, embedding_cached, embedding_ms = await _get_embedding(
            profile_text,
            client=embedding_client,
            cache=cache,
            cache_path=cache_path,
            cache_only=args.cache_only,
        )
        if not profile_embedding:
            if args.cache_only:
                print(f"[skip] {window.display_name} embedding_cache_missing")
            continue
        samples.append(
            SceneSample(
                index=index,
                session_id=window.session_id,
                display_name=window.display_name,
                window_hash=_window_hash(window.messages),
                context_preview=_context_preview(window.messages),
                profile=profile,
                distribution=distribution,
                profile_text=profile_text,
                profile_embedding=profile_embedding,
                analysis_ms=analysis_ms,
                embedding_ms=embedding_ms,
                profile_cached=profile_cached,
                embedding_cached=embedding_cached,
            )
        )
        print(
            f"[{len(samples)}/{len(windows)}] {window.display_name} "
            f"profile_cached={profile_cached} embedding_cached={embedding_cached}"
        )
    return samples


def _summary_for_build(result: BuildResult, judgements: dict[int, dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(assignment.action for assignment in result.assignments)
    cluster_sizes = [len(cluster.source_indices) for cluster in result.clusters]
    multi_clusters = [cluster for cluster in result.clusters if len(cluster.source_indices) > 1]
    judged_scores = [
        float(judgements[cluster.cluster_id].get("score") or 0.0)
        for cluster in multi_clusters
        if cluster.cluster_id in judgements
    ]
    return {
        "strategy": result.strategy,
        "cluster_count": len(result.clusters),
        "singleton_count": sum(1 for size in cluster_sizes if size == 1),
        "multi_cluster_count": len(multi_clusters),
        "max_cluster_size": max(cluster_sizes, default=0),
        "avg_cluster_size": round(sum(cluster_sizes) / len(cluster_sizes), 4) if cluster_sizes else 0.0,
        "create_count": action_counts.get("create", 0),
        "tag_merge_count": action_counts.get("tag_merge", 0),
        "embedding_merge_count": action_counts.get("embedding_merge", 0),
        "avg_judged_coherence": round(sum(judged_scores) / len(judged_scores), 4) if judged_scores else 0.0,
        "low_coherence_count": sum(1 for score in judged_scores if score < 3.0),
        "build_elapsed_ms": result.elapsed_ms,
    }


async def _judge_build(
    result: BuildResult,
    *,
    samples_by_index: dict[int, SceneSample],
    client: LLMServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
    max_judge_clusters: int,
) -> dict[int, dict[str, Any]]:
    multi_clusters = [cluster for cluster in result.clusters if len(cluster.source_indices) > 1]
    multi_clusters.sort(key=lambda cluster: len(cluster.source_indices), reverse=True)
    selected_clusters = multi_clusters[:max_judge_clusters] if max_judge_clusters > 0 else multi_clusters
    judgements: dict[int, dict[str, Any]] = {}
    for cluster in selected_clusters:
        judgement, cached, elapsed_ms = await _judge_cluster_coherence(
            strategy=result.strategy,
            cluster=cluster,
            samples_by_index=samples_by_index,
            client=client,
            cache=cache,
            cache_path=cache_path,
        )
        judgement = dict(judgement)
        judgement["cached"] = cached
        judgement["elapsed_ms"] = elapsed_ms
        judgements[cluster.cluster_id] = judgement
    return judgements


def _assignment_map(result: BuildResult) -> dict[int, Assignment]:
    return {assignment.sample_index: assignment for assignment in result.assignments}


def _changed_embedding_merges(
    *,
    original: BuildResult,
    hybrid: BuildResult,
    samples_by_index: dict[int, SceneSample],
) -> list[dict[str, Any]]:
    original_assignments = _assignment_map(original)
    cluster_by_id = {cluster.cluster_id: cluster for cluster in hybrid.clusters}
    rows: list[dict[str, Any]] = []
    for assignment in hybrid.assignments:
        if assignment.action != "embedding_merge":
            continue
        sample = samples_by_index.get(assignment.sample_index)
        original_assignment = original_assignments.get(assignment.sample_index)
        hybrid_cluster = cluster_by_id.get(assignment.cluster_id)
        if sample is None or original_assignment is None or hybrid_cluster is None:
            continue
        rows.append(
            {
                "sample_index": sample.index,
                "display_name": sample.display_name,
                "summary": sample.profile.summary,
                "original_action": original_assignment.action,
                "hybrid_cluster_id": assignment.cluster_id,
                "tag_overlap": assignment.tag_overlap,
                "embedding_score": assignment.embedding_score,
                "cluster_members": [
                    {
                        "sample_index": member_index,
                        "summary": samples_by_index[member_index].profile.summary,
                    }
                    for member_index in hybrid_cluster.source_indices
                    if member_index in samples_by_index
                ],
            }
        )
    return rows


def _cluster_payloads(
    result: BuildResult,
    *,
    samples_by_index: dict[int, SceneSample],
    judgements: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    clusters = sorted(result.clusters, key=lambda cluster: len(cluster.source_indices), reverse=True)
    payloads: list[dict[str, Any]] = []
    for cluster in clusters:
        payload = _cluster_brief(cluster, samples_by_index)
        payload["judgement"] = judgements.get(cluster.cluster_id, {})
        payloads.append(payload)
    return payloads


def _write_reports(
    *,
    args: Namespace,
    samples: Sequence[SceneSample],
    original: BuildResult,
    hybrid: BuildResult,
    original_judgements: dict[int, dict[str, Any]],
    hybrid_judgements: dict[int, dict[str, Any]],
    output_path: Path,
    json_output_path: Path,
) -> None:
    samples_by_index = {sample.index: sample for sample in samples}
    original_summary = _summary_for_build(original, original_judgements)
    hybrid_summary = _summary_for_build(hybrid, hybrid_judgements)
    changed_merges = _changed_embedding_merges(original=original, hybrid=hybrid, samples_by_index=samples_by_index)
    analysis_ms = [sample.analysis_ms for sample in samples]
    embedding_ms = [sample.embedding_ms for sample in samples]
    payload = {
        "config": {
            "samples": args.samples,
            "actual_samples": len(samples),
            "window_size": args.window_size,
            "step": args.step,
            "seed": args.seed,
            "temperature": args.temperature,
            "embedding_threshold": args.embedding_threshold,
            "tag_reuse_threshold": SCENE_CLUSTER_REUSE_THRESHOLD,
        },
        "timing": {
            "avg_scene_analysis_ms": round(sum(analysis_ms) / len(analysis_ms), 4) if analysis_ms else 0.0,
            "avg_profile_embedding_ms": round(sum(embedding_ms) / len(embedding_ms), 4) if embedding_ms else 0.0,
            "profile_cache_hits": sum(1 for sample in samples if sample.profile_cached),
            "embedding_cache_hits": sum(1 for sample in samples if sample.embedding_cached),
        },
        "summary": {
            original.strategy: original_summary,
            hybrid.strategy: hybrid_summary,
        },
        "embedding_merges": changed_merges,
        "samples": [
            {
                "sample_index": sample.index,
                "session_id": sample.session_id,
                "display_name": sample.display_name,
                "summary": sample.profile.summary,
                "distribution_text": format_scene_cluster_distribution(sample.distribution),
                "context_preview": sample.context_preview,
            }
            for sample in samples
        ],
        "clusters": {
            original.strategy: _cluster_payloads(
                original,
                samples_by_index=samples_by_index,
                judgements=original_judgements,
            ),
            hybrid.strategy: _cluster_payloads(
                hybrid,
                samples_by_index=samples_by_index,
                judgements=hybrid_judgements,
            ),
        },
    }

    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# 行为场景学习建库 AB 测试")
    lines.append("")
    lines.append("## 配置")
    lines.append("")
    lines.append(f"- 样本数：{len(samples)} / 目标 {args.samples}")
    lines.append(f"- 窗口大小：{args.window_size}")
    lines.append(f"- tag 合并阈值：{SCENE_CLUSTER_REUSE_THRESHOLD}")
    lines.append(f"- embedding fallback 阈值：{args.embedding_threshold}")
    lines.append(f"- profile 缓存命中：{payload['timing']['profile_cache_hits']}")
    lines.append(f"- embedding 缓存命中：{payload['timing']['embedding_cache_hits']}")
    lines.append(f"- 平均场景分析耗时：{payload['timing']['avg_scene_analysis_ms']} ms")
    lines.append(f"- 平均 profile embedding 耗时：{payload['timing']['avg_profile_embedding_ms']} ms")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 策略 | 场景簇数 | 单例簇 | 多样本簇 | 新建 | tag 合并 | embedding 合并 | 最大簇 | 平均簇大小 | 内部一致性均分 | 低一致性簇 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for summary in (original_summary, hybrid_summary):
        lines.append(
            f"| {summary['strategy']} | {summary['cluster_count']} | {summary['singleton_count']} | "
            f"{summary['multi_cluster_count']} | {summary['create_count']} | {summary['tag_merge_count']} | "
            f"{summary['embedding_merge_count']} | {summary['max_cluster_size']} | {summary['avg_cluster_size']} | "
            f"{summary['avg_judged_coherence']} | {summary['low_coherence_count']} |"
        )
    lines.append("")
    lines.append("## Embedding 补救合并")
    lines.append("")
    if not changed_merges:
        lines.append("本次没有出现 embedding fallback 合并。")
    else:
        for row in changed_merges[:12]:
            lines.append(
                f"### sample #{row['sample_index']} {row['display_name']} "
                f"score={row['embedding_score']} tag_overlap={row['tag_overlap']}"
            )
            lines.append("")
            lines.append(f"- 当前场景：{row['summary']}")
            lines.append("- 合并后簇成员：")
            for member in row["cluster_members"]:
                lines.append(f"  - #{member['sample_index']} {member['summary']}")
            lines.append("")
    lines.append("## 原版大簇")
    lines.append("")
    _append_cluster_sections(lines, original, samples_by_index, original_judgements)
    lines.append("## Hybrid 大簇")
    lines.append("")
    _append_cluster_sections(lines, hybrid, samples_by_index, hybrid_judgements)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _append_cluster_sections(
    lines: list[str],
    result: BuildResult,
    samples_by_index: dict[int, SceneSample],
    judgements: dict[int, dict[str, Any]],
    *,
    limit: int = 10,
) -> None:
    clusters = sorted(result.clusters, key=lambda cluster: len(cluster.source_indices), reverse=True)
    for cluster in clusters[:limit]:
        judgement = judgements.get(cluster.cluster_id, {})
        score_text = judgement.get("score", "")
        reason_text = judgement.get("reason", "")
        lines.append(
            f"### cluster #{cluster.cluster_id} count={len(cluster.source_indices)} "
            f"coherence={score_text}"
        )
        lines.append("")
        lines.append(f"- 聊天流：{cluster.display_name}")
        lines.append(f"- 分布：{format_scene_cluster_distribution(cluster.distribution)}")
        if reason_text:
            lines.append(f"- 质检：{reason_text}")
        lines.append("- 成员：")
        for source_index in cluster.source_indices[:8]:
            sample = samples_by_index.get(source_index)
            if sample is None:
                continue
            lines.append(f"  - #{sample.index} {sample.profile.summary}")
        lines.append("")


async def _run(args: Namespace) -> None:
    cache_path = Path(args.cache)
    output_path = Path(args.output)
    json_output_path = Path(args.json_output)
    cache = _load_cache(cache_path)
    windows = _select_windows(args)
    if not windows:
        raise RuntimeError("没有找到可用聊天窗口")

    with get_db_session(auto_commit=False) as session:
        tag_lookup = _load_tag_cluster_lookup(session)

    scene_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    embedding_client = EmbeddingServiceClient(task_name="embedding", request_type="behavior.scene_learning_merge")
    judge_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_learning_merge_judge")
    samples = await _collect_samples(
        windows=windows,
        args=args,
        cache=cache,
        cache_path=cache_path,
        scene_client=scene_client,
        embedding_client=embedding_client,
        tag_lookup=tag_lookup,
    )
    if not samples:
        raise RuntimeError("场景分析没有产出可用样本")

    original = _build_original_library(samples, tag_lookup=tag_lookup)
    hybrid = _build_hybrid_library(
        samples,
        tag_lookup=tag_lookup,
        embedding_threshold=args.embedding_threshold,
    )
    samples_by_index = {sample.index: sample for sample in samples}
    original_judgements = await _judge_build(
        original,
        samples_by_index=samples_by_index,
        client=judge_client,
        cache=cache,
        cache_path=cache_path,
        max_judge_clusters=args.max_judge_clusters,
    )
    hybrid_judgements = await _judge_build(
        hybrid,
        samples_by_index=samples_by_index,
        client=judge_client,
        cache=cache,
        cache_path=cache_path,
        max_judge_clusters=args.max_judge_clusters,
    )
    _write_reports(
        args=args,
        samples=samples,
        original=original,
        hybrid=hybrid,
        original_judgements=original_judgements,
        hybrid_judgements=hybrid_judgements,
        output_path=output_path,
        json_output_path=json_output_path,
    )
    print(f"Markdown report: {output_path.resolve()}")
    print(f"JSON report: {json_output_path.resolve()}")


def _parse_args() -> Namespace:
    parser = ArgumentParser(description="对比原版 tag 建库与 tag+embedding fallback 建库的场景簇质量。")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--window-size", type=int, default=30)
    parser.add_argument("--step", type=int, default=45)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--embedding-threshold", type=float, default=0.82)
    parser.add_argument("--max-judge-clusters", type=int, default=24)
    parser.add_argument("--cache-only", action="store_true", help="只使用已有缓存，缺失场景分析/embedding 时跳过样本。")
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()

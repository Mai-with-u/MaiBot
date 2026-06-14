from argparse import ArgumentParser, Namespace
from collections import defaultdict
from dataclasses import dataclass
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
from src.common.database.database_model import BehaviorSceneCluster, BehaviorSceneTagCluster, ChatSession, Messages  # noqa: E402
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import BehaviorScenarioProfile, parse_behavior_scenario_segments_response  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    _load_cluster_distribution,
    _load_tag_cluster_lookup,
    debug_retrieve_behavior_scores_from_scene_clusters,
    format_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.embedding_service import EmbeddingServiceClient  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_scene_embedding_match_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_scene_embedding_match_abtest.json"
DEFAULT_CACHE = "data/analysis/behavior_scene_embedding_match_abtest_cache.json"


@dataclass(frozen=True)
class SourceWindow:
    session_id: str
    display_name: str
    messages: list[SessionMessage]


@dataclass(frozen=True)
class ClusterDoc:
    cluster_id: int
    session_id: str | None
    text: str
    display_text: str
    source_count: int


@dataclass(frozen=True)
class MatchResult:
    method: str
    top_clusters: list[dict[str, Any]]
    elapsed_ms: dict[str, float]


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
        .order_by(func.count(Messages.id).desc())
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


def _message_context_text(messages: Sequence[SessionMessage], *, max_message_length: int = 220) -> str:
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
) -> tuple[BehaviorScenarioProfile | None, str, bool, float]:
    cache_key = f"scene_profile:{_window_hash(window.messages)}:t{temperature}"
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict) and isinstance(cached_item.get("raw_response"), str)
    start = perf_counter()
    if cached:
        raw_response = str(cached_item.get("raw_response") or "")
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
    elapsed_ms = _round_ms(perf_counter() - start)
    return _parse_first_profile(raw_response), raw_response, cached, elapsed_ms


def _profile_payload(profile: BehaviorScenarioProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"summary": "", "tag_clusters": [], "need": {}, "other_traits": [], "confidence": 0.0}
    return {
        "summary": profile.summary,
        "tag_clusters": profile.domain_prompt_payloads(),
        "need": profile.need_prompt_payload(),
        "other_traits": profile.other_traits_prompt_payloads(),
        "confidence": profile.confidence,
        "tag_key": profile.tag_cluster_text(),
    }


def _profile_text(profile: BehaviorScenarioProfile | None) -> str:
    payload = _profile_payload(profile)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _tag_display_lookup(session: Session) -> dict[tuple[str, str], list[str]]:
    rows = session.exec(select(BehaviorSceneTagCluster)).all()
    values: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        key = (str(row.tag_kind or ""), str(row.cluster_key or ""))
        tag = str(row.tag or "").strip()
        if key[0] and key[1] and tag and tag not in values[key]:
            values[key].append(tag)
    return values


def _cluster_text(
    cluster: BehaviorSceneCluster,
    *,
    display_lookup: dict[tuple[str, str], list[str]],
    tag_lookup: dict[tuple[str, str], str],
) -> tuple[str, str]:
    distribution = _load_cluster_distribution(cluster.tag_distribution)
    parts: list[str] = []
    for item in distribution:
        raw_tag = str(item.get("tag") or "").strip()
        if ":" not in raw_tag:
            continue
        tag_kind, cluster_key = raw_tag.split(":", 1)
        display_values = display_lookup.get((tag_kind, cluster_key), [])
        label = display_values[0] if display_values else raw_tag
        aliases = "、".join(display_values[1:5])
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            probability = 0.0
        parts.append(f"{label}({aliases}) p={probability:.3f}" if aliases else f"{label} p={probability:.3f}")
    display_text = "；".join(parts) or format_scene_cluster_distribution(distribution, tag_lookup=tag_lookup)
    text = f"行为场景簇：{display_text}"
    return text, display_text


def _load_cluster_docs(
    *,
    session_ids: set[str],
    include_global: bool,
) -> list[ClusterDoc]:
    with get_db_session(auto_commit=False) as session:
        tag_lookup = _load_tag_cluster_lookup(session)
        display_lookup = _tag_display_lookup(session)
        statement = select(BehaviorSceneCluster)
        clusters = [
            cluster
            for cluster in session.exec(statement).all()
            if include_global or cluster.session_id in session_ids or cluster.session_id is None
        ]
        docs: list[ClusterDoc] = []
        for cluster in clusters:
            if cluster.id is None:
                continue
            text, display_text = _cluster_text(cluster, display_lookup=display_lookup, tag_lookup=tag_lookup)
            docs.append(
                ClusterDoc(
                    cluster_id=int(cluster.id),
                    session_id=cluster.session_id,
                    text=text,
                    display_text=display_text,
                    source_count=int(cluster.source_count or 0),
                )
            )
    return docs


def _embedding_cache_key(text: str) -> str:
    return "embedding:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _get_embedding(
    text: str,
    *,
    client: EmbeddingServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
) -> tuple[list[float], bool, float]:
    cache_key = _embedding_cache_key(text)
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict) and isinstance(cached_item.get("embedding"), list)
    start = perf_counter()
    if cached:
        embedding = [float(value) for value in cached_item["embedding"]]
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


async def _embedding_match(
    *,
    method: str,
    query_text: str,
    docs: Sequence[ClusterDoc],
    embedding_client: EmbeddingServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
    max_count: int,
) -> MatchResult:
    query_embedding, query_cached, query_ms = await _get_embedding(
        query_text,
        client=embedding_client,
        cache=cache,
        cache_path=cache_path,
    )
    cluster_embedding_ms = 0.0
    cluster_cache_miss = 0
    cluster_vectors: list[tuple[ClusterDoc, list[float]]] = []
    for doc in docs:
        embedding, cached, elapsed_ms = await _get_embedding(
            doc.text,
            client=embedding_client,
            cache=cache,
            cache_path=cache_path,
        )
        cluster_embedding_ms += elapsed_ms
        if not cached:
            cluster_cache_miss += 1
        cluster_vectors.append((doc, embedding))

    similarity_start = perf_counter()
    scored = [
        {
            "cluster_id": doc.cluster_id,
            "session_id": doc.session_id,
            "name": doc.display_text,
            "score": round(_cosine(query_embedding, embedding), 4),
            "source_count": doc.source_count,
        }
        for doc, embedding in cluster_vectors
    ]
    scored.sort(key=lambda item: item["score"], reverse=True)
    similarity_ms = _round_ms(perf_counter() - similarity_start)
    return MatchResult(
        method=method,
        top_clusters=scored[:max_count],
        elapsed_ms={
            "query_embedding_ms": query_ms,
            "similarity_ms": similarity_ms,
            "cluster_embedding_ms": round(cluster_embedding_ms, 2),
            "cluster_cache_miss": float(cluster_cache_miss),
            "query_cached": 1.0 if query_cached else 0.0,
        },
    )


def _main_match(
    *,
    profile: BehaviorScenarioProfile | None,
    session_ids: set[str],
    include_global: bool,
    max_count: int,
    retrieval_mode: str,
) -> MatchResult:
    if profile is None:
        return MatchResult(method="main_tag_spread", top_clusters=[], elapsed_ms={"main_match_ms": 0.0})
    start = perf_counter()
    debug_result = debug_retrieve_behavior_scores_from_scene_clusters(
        session_ids=session_ids,
        include_global=include_global,
        profile=profile,
        max_count=max_count,
        retrieval_mode=retrieval_mode,
    )
    elapsed_ms = _round_ms(perf_counter() - start)
    return MatchResult(
        method="main_tag_spread",
        top_clusters=[
            {
                "cluster_id": int(item["cluster_id"]),
                "name": str(item.get("name") or ""),
                "score": float(item.get("score") or 0.0),
            }
            for item in debug_result.get("matched_clusters", [])[:max_count]
            if item.get("cluster_id") is not None
        ],
        elapsed_ms={"main_match_ms": elapsed_ms},
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


async def _judge_match(
    *,
    window: SourceWindow,
    profile: BehaviorScenarioProfile | None,
    method: str,
    top_cluster: dict[str, Any] | None,
    client: LLMServiceClient,
    cache: dict[str, Any],
    cache_path: Path,
) -> tuple[dict[str, Any], bool, float]:
    if not top_cluster:
        return {"score": 0.0, "reason": "无候选场景簇"}, True, 0.0
    judge_payload = {
        "context": _message_context_text(window.messages, max_message_length=120),
        "profile": _profile_payload(profile),
        "method": method,
        "cluster": {
            "cluster_id": top_cluster.get("cluster_id"),
            "name": top_cluster.get("name"),
            "score": top_cluster.get("score"),
        },
    }
    cache_key = "judge:" + hashlib.sha256(
        json.dumps(judge_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict)
    start = perf_counter()
    if cached:
        return cached_item, True, _round_ms(perf_counter() - start)

    prompt = (
        "你是行为场景匹配评估器。请判断候选场景簇是否精准匹配聊天窗口的真实互动场景。\n"
        "评分标准：5=主题、对象、互动需求都很准；4=主要主题准确但略泛；3=部分相关但漏掉关键主题；"
        "2=只有宽泛氛围相关；1=基本不相关；0=完全错误。\n"
        "只输出 JSON：{\"score\": 0-5, \"reason\": \"简短原因\"}。"
    )
    messages = [
        MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build(),
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content(json.dumps(judge_payload, ensure_ascii=False, indent=2))
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
    score = max(0.0, min(5.0, score))
    payload = {
        "score": round(score, 2),
        "reason": str(parsed.get("reason") or "").strip()[:300],
        "raw_response": result.response or "",
    }
    cache[cache_key] = payload
    _save_cache(cache_path, cache)
    return payload, False, _round_ms(perf_counter() - start)


def _top_cluster_ids(result: MatchResult, limit: int = 5) -> list[int]:
    return [int(item["cluster_id"]) for item in result.top_clusters[:limit] if item.get("cluster_id") is not None]


def _jaccard(left: Sequence[int], right: Sequence[int]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    union = left_set | right_set
    if not union:
        return 0.0
    return round(len(left_set & right_set) / len(union), 4)


async def _build_sample(
    *,
    index: int,
    window: SourceWindow,
    args: Namespace,
    cache: dict[str, Any],
    cache_path: Path,
    scene_client: LLMServiceClient,
    embedding_client: EmbeddingServiceClient,
    judge_client: LLMServiceClient,
) -> dict[str, Any]:
    profile, raw_response, profile_cached, scene_analysis_ms = await _analyze_profile(
        window,
        client=scene_client,
        cache=cache,
        cache_path=cache_path,
        temperature=args.temperature,
    )
    session_ids = set() if args.all_sessions else {window.session_id}
    include_global = bool(args.all_sessions or args.include_global)
    docs = _load_cluster_docs(session_ids=session_ids, include_global=include_global)
    main_result = _main_match(
        profile=profile,
        session_ids=session_ids,
        include_global=include_global,
        max_count=args.max_count,
        retrieval_mode=args.retrieval_mode,
    )
    raw_embedding_result = await _embedding_match(
        method="embedding_raw_context",
        query_text=_message_context_text(window.messages),
        docs=docs,
        embedding_client=embedding_client,
        cache=cache,
        cache_path=cache_path,
        max_count=args.max_count,
    )
    profile_embedding_result = await _embedding_match(
        method="embedding_profile_text",
        query_text=_profile_text(profile),
        docs=docs,
        embedding_client=embedding_client,
        cache=cache,
        cache_path=cache_path,
        max_count=args.max_count,
    )
    results = [main_result, raw_embedding_result, profile_embedding_result]
    judgements: dict[str, Any] = {}
    judge_times: dict[str, float] = {}
    judge_cached: dict[str, bool] = {}
    for result in results:
        judgement, cached, elapsed_ms = await _judge_match(
            window=window,
            profile=profile,
            method=result.method,
            top_cluster=result.top_clusters[0] if result.top_clusters else None,
            client=judge_client,
            cache=cache,
            cache_path=cache_path,
        )
        judgements[result.method] = judgement
        judge_times[result.method] = elapsed_ms
        judge_cached[result.method] = cached
    scores = {method: float(item.get("score") or 0.0) for method, item in judgements.items()}
    best_score = max(scores.values(), default=0.0)
    winners = [method for method, score in scores.items() if score == best_score]
    return {
        "index": index,
        "session_id": window.session_id,
        "chat_name": window.display_name,
        "message_count": len(window.messages),
        "time_range": {
            "start": window.messages[0].timestamp.isoformat(timespec="seconds"),
            "end": window.messages[-1].timestamp.isoformat(timespec="seconds"),
        },
        "profile_cached": profile_cached,
        "scene_analysis_ms": scene_analysis_ms,
        "profile": _profile_payload(profile),
        "context_preview": _message_context_text(window.messages, max_message_length=100).splitlines()[:18],
        "cluster_count": len(docs),
        "methods": {
            result.method: {
                "top_clusters": result.top_clusters[:5],
                "elapsed_ms": result.elapsed_ms,
                "judge": judgements[result.method],
                "judge_ms": judge_times[result.method],
                "judge_cached": judge_cached[result.method],
            }
            for result in results
        },
        "compare": {
            "winner": winners,
            "scores": scores,
            "raw_top5_jaccard_with_main": _jaccard(_top_cluster_ids(main_result), _top_cluster_ids(raw_embedding_result)),
            "profile_top5_jaccard_with_main": _jaccard(
                _top_cluster_ids(main_result),
                _top_cluster_ids(profile_embedding_result),
            ),
        },
        "raw_scene_response": raw_response if args.keep_raw else "",
    }


def _avg(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / float(len(values)), 4)


def _summarize(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    methods = ["main_tag_spread", "embedding_raw_context", "embedding_profile_text"]
    summary: dict[str, Any] = {}
    for method in methods:
        method_items = [sample["methods"][method] for sample in samples if method in sample["methods"]]
        summary[method] = {
            "avg_judge_score": _avg([float(item["judge"].get("score") or 0.0) for item in method_items]),
            "win_count": sum(1 for sample in samples if method in sample["compare"]["winner"]),
            "avg_judge_ms": _avg([float(item.get("judge_ms") or 0.0) for item in method_items]),
        }
        elapsed_keys = sorted({key for item in method_items for key in item.get("elapsed_ms", {})})
        for key in elapsed_keys:
            summary[method][f"avg_{key}"] = _avg([float(item["elapsed_ms"].get(key) or 0.0) for item in method_items])
    summary["overall"] = {
        "avg_scene_analysis_ms": _avg([float(sample.get("scene_analysis_ms") or 0.0) for sample in samples]),
        "avg_raw_top5_jaccard_with_main": _avg(
            [float(sample["compare"].get("raw_top5_jaccard_with_main") or 0.0) for sample in samples]
        ),
        "avg_profile_top5_jaccard_with_main": _avg(
            [float(sample["compare"].get("profile_top5_jaccard_with_main") or 0.0) for sample in samples]
        ),
    }
    return summary


async def build_report(args: Namespace) -> dict[str, Any]:
    cache_path = PROJECT_ROOT / args.cache
    cache = _load_cache(cache_path)
    windows = _select_windows(args)
    if len(windows) < args.samples:
        raise ValueError(f"可用样本不足: 请求 {args.samples}，实际 {len(windows)}")
    scene_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    judge_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_match_judge")
    embedding_client = EmbeddingServiceClient(task_name="embedding", request_type="behavior.scene_embedding_match")
    samples: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        samples.append(
            await _build_sample(
                index=index,
                window=window,
                args=args,
                cache=cache,
                cache_path=cache_path,
                scene_client=scene_client,
                embedding_client=embedding_client,
                judge_client=judge_client,
            )
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested_samples": args.samples,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "temperature": args.temperature,
        "seed": args.seed,
        "scope": "all_sessions" if args.all_sessions else "sample_session_only",
        "include_global": bool(args.all_sessions or args.include_global),
        "retrieval_mode": args.retrieval_mode,
        "cache": str(cache_path),
        "summary": _summarize(samples),
        "samples": samples,
    }


def _cluster_line(cluster: dict[str, Any]) -> str:
    return f"#{cluster.get('cluster_id')} score={cluster.get('score')} {cluster.get('name')}"


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# 行为场景匹配：主程序 vs Embedding AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 样本数：{report['sample_count']} / 请求 {report['requested_samples']}")
    lines.append(f"- 窗口大小：{report['window_size']}")
    lines.append(f"- temperature：{report['temperature']}")
    lines.append(f"- retrieval_mode：`{report['retrieval_mode']}`")
    lines.append(f"- 范围：{report['scope']}，include_global={report['include_global']}")
    lines.append(f"- 缓存：`{report['cache']}`")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 方法 | 平均判分 | 胜出数 | 在线匹配耗时 | query embedding | 相似度耗时 | cluster embedding 冷耗时 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    labels = {
        "main_tag_spread": "主程序 tag 扩散",
        "embedding_raw_context": "Embedding 原始窗口",
        "embedding_profile_text": "Embedding 场景画像",
    }
    for method, label in labels.items():
        item = report["summary"].get(method, {})
        lines.append(
            f"| {label} | {item.get('avg_judge_score', 0)} | {item.get('win_count', 0)} | "
            f"{item.get('avg_main_match_ms', 0)} | {item.get('avg_query_embedding_ms', 0)} | "
            f"{item.get('avg_similarity_ms', 0)} | {item.get('avg_cluster_embedding_ms', 0)} |"
        )
    overall = report["summary"].get("overall", {})
    lines.append("")
    lines.append(f"- 平均场景分析耗时：{overall.get('avg_scene_analysis_ms', 0)} ms")
    lines.append(f"- Embedding 原始窗口 Top5 与主程序平均 Jaccard：{overall.get('avg_raw_top5_jaccard_with_main', 0)}")
    lines.append(f"- Embedding 画像 Top5 与主程序平均 Jaccard：{overall.get('avg_profile_top5_jaccard_with_main', 0)}")
    lines.append("")

    for sample in report["samples"]:
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append(f"- 场景簇候选数：{sample['cluster_count']}")
        lines.append(f"- 场景分析耗时：{sample['scene_analysis_ms']} ms，cached={sample['profile_cached']}")
        lines.append(f"- 胜者：{', '.join(sample['compare']['winner'])}")
        lines.append(f"- 分数：`{json.dumps(sample['compare']['scores'], ensure_ascii=False)}`")
        lines.append(f"- summary：{sample['profile'].get('summary') or '-'}")
        lines.append("")
        for method, label in labels.items():
            item = sample["methods"].get(method, {})
            judge = item.get("judge", {})
            lines.append(f"### {label}")
            lines.append(f"- 判分：{judge.get('score')}，原因：{judge.get('reason')}")
            lines.append(f"- 耗时：`{json.dumps(item.get('elapsed_ms', {}), ensure_ascii=False)}`")
            for cluster in item.get("top_clusters", [])[:3]:
                lines.append(f"- {_cluster_line(cluster)}")
            lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="对比当前行为场景 tag 匹配与 embedding 场景簇匹配，并统计耗时。")
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--window-size", type=int, default=30)
    parser.add_argument("--step", type=int, default=45)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-count", type=int, default=10)
    parser.add_argument(
        "--retrieval-mode",
        choices=["direct_domain_overlap", "tag_cluster_spread_1", "tag_cluster_spread_2"],
        default="tag_cluster_spread_1",
    )
    parser.add_argument("--include-global", action="store_true")
    parser.add_argument("--all-sessions", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    args = parse_args()
    report = asyncio.run(build_report(args))
    output_path = PROJECT_ROOT / args.output
    json_output_path = PROJECT_ROOT / args.json_output
    write_markdown_report(report, output_path)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Markdown report: {output_path}")
    print(f"JSON report: {json_output_path}")


if __name__ == "__main__":
    main()

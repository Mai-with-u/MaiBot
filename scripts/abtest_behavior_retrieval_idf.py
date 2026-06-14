from argparse import ArgumentParser, Namespace
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import log
from pathlib import Path
from random import Random
from typing import Any, Sequence

import asyncio
import json
import sys

from sqlmodel import Session, col, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
    BehaviorSceneCluster,
    BehaviorSceneTagCluster,
    ChatSession,
    Messages,
)
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import (  # noqa: E402
    BehaviorScenarioProfile,
    parse_behavior_scenario_segments_response,
)
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    DIRECT_LOCK_THRESHOLD,
    LOCKED_DIRECT_SPREAD_FACTOR,
    TAG_CLUSTER_SPREAD_TOPK,
    _build_tag_cluster_adjacency,
    _distribution_to_mapping,
    _expand_tag_cluster_weights,
    _load_cluster_distribution,
    _load_tag_cluster_lookup,
    _scene_cluster_matches_sessions,
    _score_behavior_clusters,
    build_scene_cluster_distribution,
    format_scene_cluster_distribution,
    retrieve_behavior_scores_from_scene_clusters,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_retrieval_idf_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_retrieval_idf_abtest.json"
DEFAULT_TOP_COUNT = 8


@dataclass(frozen=True)
class MessageSample:
    message_id: str
    timestamp: datetime
    session_id: str
    chat_name: str
    speaker: str
    text: str


@dataclass(frozen=True)
class ScopedClusters:
    clusters: list[BehaviorSceneCluster]
    tag_lookup: dict[tuple[str, str], str]
    idf_by_tag: dict[str, float]
    df_by_tag: dict[str, int]


def _chat_display_name(session: ChatSession | None, session_id: str) -> str:
    if session is None:
        return session_id
    if session.group_name:
        return session.group_name
    if session.user_nickname:
        return f"{session.user_nickname} 的私聊"
    return session.session_id


def _clean_text(text: str, *, max_length: int = 220) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip() + "..."


def _load_recent_messages(days: int, *, min_text_length: int = 4) -> dict[str, list[MessageSample]]:
    cutoff = datetime.now() - timedelta(days=days)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(Messages)
            .where(Messages.timestamp >= cutoff)
            .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
            .order_by(Messages.timestamp.asc())  # type: ignore[attr-defined]
        ).all()
        session_ids = {row.session_id for row in rows if row.session_id}
        chat_sessions: dict[str, ChatSession] = {}
        if session_ids:
            chat_sessions = {
                chat.session_id: chat
                for chat in session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(session_ids))).all()
            }

    grouped_messages: dict[str, list[MessageSample]] = defaultdict(list)
    for row in rows:
        text = _clean_text(row.processed_plain_text or "")
        if len(text) < min_text_length:
            continue
        grouped_messages[row.session_id].append(
            MessageSample(
                message_id=row.message_id,
                timestamp=row.timestamp,
                session_id=row.session_id,
                chat_name=_chat_display_name(chat_sessions.get(row.session_id), row.session_id),
                speaker=row.user_cardname or row.user_nickname or row.user_id,
                text=text,
            )
        )
    return dict(grouped_messages)


def _sample_windows(
    grouped_messages: dict[str, list[MessageSample]],
    *,
    sample_count: int,
    window_size: int,
    seed: int,
    max_gap_minutes: int,
) -> list[list[MessageSample]]:
    random = Random(seed)
    candidates: list[list[MessageSample]] = []
    max_gap = timedelta(minutes=max_gap_minutes)
    for messages in grouped_messages.values():
        chunk: list[MessageSample] = []
        for message in messages:
            if chunk and message.timestamp - chunk[-1].timestamp > max_gap:
                if len(chunk) >= max(3, min(window_size, 6)):
                    candidates.append(chunk)
                chunk = []
            chunk.append(message)
        if len(chunk) >= max(3, min(window_size, 6)):
            candidates.append(chunk)

    random.shuffle(candidates)
    windows: list[list[MessageSample]] = []
    for messages in candidates:
        if len(windows) >= sample_count:
            break
        upper_bound = max(0, len(messages) - window_size)
        start = random.randint(0, upper_bound) if upper_bound > 0 else 0
        window = messages[start : start + window_size]
        if len(window) >= 3:
            windows.append(window)
    return windows


async def _analyze_profile_with_llm(
    window: Sequence[MessageSample],
    *,
    client: LLMServiceClient,
) -> tuple[BehaviorScenarioProfile | None, str]:
    prompt = load_prompt("behavior_scene_analyze", bot_name=global_config.bot.nickname)
    messages = [MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build()]
    for index, message in enumerate(window, start=1):
        messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content(
                "\n".join(
                    [
                        f"[source_id:{index}]",
                        "[speaker:USER]",
                        f"[name:{message.speaker}]",
                        f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                        "[content]",
                        message.text or "[空消息]",
                    ]
                )
            )
            .build()
        )
    messages.append(MessageBuilder().set_role(RoleType.User).add_text_content("请根据以上聊天消息输出 JSON。").build())
    result = await client.generate_response_with_messages(
        lambda _client: messages,
        options=LLMGenerationOptions(temperature=0.2),
    )
    raw_response = result.response or ""
    segments = parse_behavior_scenario_segments_response(raw_response)
    if not segments:
        return None, raw_response
    profile = segments[0].profile
    if not profile.tag_clusters:
        return None, raw_response
    return profile, raw_response


def _profile_payload(profile: BehaviorScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "tag_clusters": profile.domain_prompt_payloads(),
        "need": profile.need_prompt_payload(),
        "other_traits": profile.other_traits_prompt_payloads(),
        "confidence": profile.confidence,
        "tag_key": profile.tag_cluster_text(),
    }


def _message_payload(window: Sequence[MessageSample]) -> list[dict[str, str]]:
    return [
        {
            "time": message.timestamp.isoformat(timespec="seconds"),
            "speaker": message.speaker,
            "text": message.text,
        }
        for message in window
    ]


def _load_scoped_clusters(
    session: Session,
    *,
    session_ids: set[str],
    include_global: bool,
) -> ScopedClusters:
    tag_lookup = _load_tag_cluster_lookup(session)
    clusters = [
        cluster
        for cluster in session.exec(select(BehaviorSceneCluster)).all()
        if include_global or _scene_cluster_matches_sessions(cluster.session_id, session_ids)
    ]
    df_counter: Counter[str] = Counter()
    for cluster in clusters:
        cluster_tags = _distribution_to_mapping(_load_cluster_distribution(cluster.tag_distribution), tag_lookup=tag_lookup)
        for tag in cluster_tags:
            df_counter[tag] += 1

    cluster_count = max(len(clusters), 1)
    idf_by_tag = {
        tag: 1.0 + log((float(cluster_count) + 1.0) / (float(df_count) + 1.0))
        for tag, df_count in df_counter.items()
        if df_count > 0
    }
    return ScopedClusters(
        clusters=clusters,
        tag_lookup=tag_lookup,
        idf_by_tag=idf_by_tag,
        df_by_tag=dict(df_counter),
    )


def _weighted_overlap_score(
    query_tag_weights: dict[str, float],
    cluster_tags: dict[str, float],
    *,
    idf_by_tag: dict[str, float],
) -> float:
    weighted_query = {
        tag: max(float(weight or 0.0), 0.0) * max(float(idf_by_tag.get(tag, 1.0)), 0.0)
        for tag, weight in query_tag_weights.items()
    }
    total_query_weight = sum(weighted_query.values())
    if total_query_weight <= 0:
        return 0.0

    shared_tags = set(weighted_query) & set(cluster_tags)
    if not shared_tags:
        return 0.0
    hit_weight = sum(weighted_query[tag] * max(float(cluster_tags.get(tag) or 0.0), 0.0) for tag in shared_tags)
    return hit_weight / total_query_weight


def _score_scene_clusters_by_idf_direct(
    *,
    profile: BehaviorScenarioProfile,
    scoped_clusters: ScopedClusters,
    topk: int,
) -> dict[int, float]:
    direct_tags = _distribution_to_mapping(
        build_scene_cluster_distribution(profile, tag_lookup=scoped_clusters.tag_lookup),
        tag_lookup=scoped_clusters.tag_lookup,
    )
    if not direct_tags:
        return {}

    cluster_scores: dict[int, float] = {}
    for cluster in scoped_clusters.clusters:
        if cluster.id is None:
            continue
        cluster_tags = _distribution_to_mapping(
            _load_cluster_distribution(cluster.tag_distribution),
            tag_lookup=scoped_clusters.tag_lookup,
        )
        score = _weighted_overlap_score(direct_tags, cluster_tags, idf_by_tag=scoped_clusters.idf_by_tag)
        if score <= 0:
            continue
        cluster_scores[int(cluster.id)] = round(score * 2.0, 4)
    return dict(sorted(cluster_scores.items(), key=lambda item: item[1], reverse=True)[:topk])


def _score_scene_clusters_by_idf_spread(
    *,
    profile: BehaviorScenarioProfile,
    scoped_clusters: ScopedClusters,
    max_depth: int,
    topk: int,
) -> dict[int, float]:
    direct_tags = _distribution_to_mapping(
        build_scene_cluster_distribution(profile, tag_lookup=scoped_clusters.tag_lookup),
        tag_lookup=scoped_clusters.tag_lookup,
    )
    if not direct_tags:
        return {}

    adjacency = _build_tag_cluster_adjacency(scoped_clusters.clusters, tag_lookup=scoped_clusters.tag_lookup)
    query_tag_weights, _ = _expand_tag_cluster_weights(direct_tags, adjacency, max_depth=max_depth)

    cluster_scores: dict[int, float] = {}
    for cluster in scoped_clusters.clusters:
        if cluster.id is None:
            continue
        cluster_tags = _distribution_to_mapping(
            _load_cluster_distribution(cluster.tag_distribution),
            tag_lookup=scoped_clusters.tag_lookup,
        )
        score = _weighted_overlap_score(query_tag_weights, cluster_tags, idf_by_tag=scoped_clusters.idf_by_tag)
        if score <= 0:
            continue
        cluster_scores[int(cluster.id)] = round(score * 2.0, 4)
    return dict(sorted(cluster_scores.items(), key=lambda item: item[1], reverse=True)[:topk])


def _retrieve_idf_scores(
    session: Session,
    *,
    session_ids: set[str],
    include_global: bool,
    profile: BehaviorScenarioProfile,
    max_count: int,
) -> dict[int, float]:
    scoped_clusters = _load_scoped_clusters(session, session_ids=session_ids, include_global=include_global)
    direct_cluster_scores = _score_scene_clusters_by_idf_direct(
        profile=profile,
        scoped_clusters=scoped_clusters,
        topk=TAG_CLUSTER_SPREAD_TOPK,
    )
    spread_cluster_scores = _score_scene_clusters_by_idf_spread(
        profile=profile,
        scoped_clusters=scoped_clusters,
        max_depth=1,
        topk=TAG_CLUSTER_SPREAD_TOPK,
    )
    direct_behavior_scores = _score_behavior_clusters(
        session,
        cluster_scores=direct_cluster_scores,
        session_ids=session_ids,
        include_global=include_global,
    )
    spread_behavior_scores = _score_behavior_clusters(
        session,
        cluster_scores=spread_cluster_scores,
        session_ids=session_ids,
        include_global=include_global,
    )

    behavior_scores: dict[int, float] = {}
    direct_top_score = max(direct_behavior_scores.values(), default=0.0)
    if direct_top_score >= DIRECT_LOCK_THRESHOLD:
        behavior_scores.update(direct_behavior_scores)
        for experience_path_id, score in spread_behavior_scores.items():
            protected_score = float(score or 0.0) * LOCKED_DIRECT_SPREAD_FACTOR
            behavior_scores[experience_path_id] = max(behavior_scores.get(experience_path_id, 0.0), protected_score)
    else:
        behavior_scores.update(spread_behavior_scores)

    return dict(sorted(behavior_scores.items(), key=lambda item: item[1], reverse=True)[:max_count])


def _tag_cluster_labels(session: Session, cluster_keys: set[str]) -> dict[tuple[str, str], list[str]]:
    if not cluster_keys:
        return {}
    rows = session.exec(
        select(BehaviorSceneTagCluster).where(BehaviorSceneTagCluster.cluster_key.in_(cluster_keys))  # type: ignore[attr-defined]
    ).all()
    labels: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if not row.tag_kind or not row.cluster_key or not row.tag:
            continue
        key = (row.tag_kind, row.cluster_key)
        labels.setdefault(key, [])
        if row.tag not in labels[key]:
            labels[key].append(row.tag)
    return labels


def _scene_cluster_summary(
    session: Session,
    scene_cluster_id: Any,
    *,
    idf_by_tag: dict[str, float],
    df_by_tag: dict[str, int],
    cluster_count: int,
) -> dict[str, Any]:
    if scene_cluster_id is None:
        return {"text": "", "avg_df_ratio": 0.0, "tags": []}
    try:
        normalized_cluster_id = int(scene_cluster_id)
    except (TypeError, ValueError):
        return {"text": "", "avg_df_ratio": 0.0, "tags": []}

    cluster = session.get(BehaviorSceneCluster, normalized_cluster_id)
    if cluster is None:
        return {"text": "", "avg_df_ratio": 0.0, "tags": []}

    distribution = _load_cluster_distribution(cluster.tag_distribution)
    tag_entries: list[tuple[str, str, float]] = []
    for item in distribution:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        if ":" not in tag:
            continue
        tag_kind, cluster_key = tag.split(":", 1)
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            probability = 0.0
        if tag_kind and cluster_key and probability > 0:
            tag_entries.append((tag_kind, cluster_key, probability))

    labels_by_key = _tag_cluster_labels(session, {cluster_key for _, cluster_key, _ in tag_entries})
    scoped_cluster_count = max(cluster_count, 1)
    tags: list[dict[str, Any]] = []
    for tag_kind, cluster_key, probability in sorted(tag_entries, key=lambda item: item[2], reverse=True)[:8]:
        tag_name = f"{tag_kind}:{cluster_key}"
        labels = labels_by_key.get((tag_kind, cluster_key), [])
        tags.append(
            {
                "tag": tag_name,
                "label": "/".join(labels[:3]) if labels else cluster_key,
                "probability": round(probability, 4),
                "df": int(df_by_tag.get(tag_name, 0)),
                "df_ratio": round(float(df_by_tag.get(tag_name, 0)) / float(scoped_cluster_count), 4),
                "idf": round(float(idf_by_tag.get(tag_name, 1.0)), 4),
            }
        )

    text_parts = [
        f"{tag['label']}={tag['probability']:.3f},df={tag['df']},idf={tag['idf']:.2f}"
        for tag in tags[:5]
    ]
    avg_df_ratio = 0.0
    if tags:
        avg_df_ratio = sum(float(tag["df_ratio"]) * float(tag["probability"]) for tag in tags)
    return {
        "text": "；".join(text_parts) or format_scene_cluster_distribution(distribution),
        "avg_df_ratio": round(avg_df_ratio, 4),
        "tags": tags,
        "source_count": cluster.source_count,
        "cluster_score": cluster.score,
    }


def _candidate_payloads(
    session: Session,
    *,
    behavior_scores: dict[int, float],
    idf_by_tag: dict[str, float],
    df_by_tag: dict[str, int],
    cluster_count: int,
    query_session_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for behavior_id, score in behavior_scores.items():
        path = session.get(BehaviorExperiencePath, behavior_id)
        if path is None:
            continue
        action = session.get(BehaviorAction, path.action_id)
        outcome = session.get(BehaviorOutcome, path.outcome_id)
        scene_summary = _scene_cluster_summary(
            session,
            path.scene_cluster_id,
            idf_by_tag=idf_by_tag,
            df_by_tag=df_by_tag,
            cluster_count=cluster_count,
        )
        candidates.append(
            {
                "id": path.id,
                "retrieval_score": round(float(score or 0.0), 4),
                "same_chat": path.session_id == query_session_id,
                "session_id": path.session_id,
                "scene_cluster_id": path.scene_cluster_id,
                "scene_tags": scene_summary["text"],
                "scene_avg_df_ratio": scene_summary["avg_df_ratio"],
                "scene_tag_details": scene_summary["tags"],
                "action": action.action if action is not None else "",
                "outcome": outcome.outcome if outcome is not None else "",
                "count": path.count,
                "success_count": path.success_count,
                "failure_count": path.failure_count,
                "path_score": path.score,
                "actor_type": path.actor_type,
                "learning_type": path.learning_type,
            }
        )
    return candidates


def _current_retrieve_scores(
    *,
    session_ids: set[str],
    include_global: bool,
    profile: BehaviorScenarioProfile,
    max_count: int,
) -> dict[int, float]:
    return retrieve_behavior_scores_from_scene_clusters(
        session_ids=session_ids,
        include_global=include_global,
        profile=profile,
        max_count=max_count,
        retrieval_mode="tag_cluster_spread_1",
    )


def _compare_candidate_sets(a_candidates: list[dict[str, Any]], b_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    a_ids = [candidate["id"] for candidate in a_candidates if candidate.get("id") is not None]
    b_ids = [candidate["id"] for candidate in b_candidates if candidate.get("id") is not None]
    a_set = set(a_ids)
    b_set = set(b_ids)
    union_count = len(a_set | b_set)
    overlap_count = len(a_set & b_set)
    return {
        "top1_changed": bool(a_ids and b_ids and a_ids[0] != b_ids[0]),
        "a_top1": a_ids[0] if a_ids else None,
        "b_top1": b_ids[0] if b_ids else None,
        "overlap_count": overlap_count,
        "jaccard": round(float(overlap_count) / float(union_count), 4) if union_count else 1.0,
        "a_avg_top_df_ratio": _avg_candidate_df_ratio(a_candidates),
        "b_avg_top_df_ratio": _avg_candidate_df_ratio(b_candidates),
    }


def _avg_candidate_df_ratio(candidates: list[dict[str, Any]], *, limit: int = 3) -> float:
    scoped_candidates = candidates[:limit]
    if not scoped_candidates:
        return 0.0
    return round(
        sum(float(candidate.get("scene_avg_df_ratio") or 0.0) for candidate in scoped_candidates)
        / float(len(scoped_candidates)),
        4,
    )


def _build_sample_result(
    *,
    index: int,
    window: Sequence[MessageSample],
    profile: BehaviorScenarioProfile,
    raw_llm_response: str,
    args: Namespace,
) -> dict[str, Any]:
    session_ids = set() if args.all_sessions else {window[0].session_id}
    include_global = args.all_sessions or args.include_global
    with get_db_session(auto_commit=False) as session:
        scoped_clusters = _load_scoped_clusters(session, session_ids=session_ids, include_global=include_global)
        current_scores = _current_retrieve_scores(
            session_ids=session_ids,
            include_global=include_global,
            profile=profile,
            max_count=args.max_count,
        )
        idf_scores = _retrieve_idf_scores(
            session,
            session_ids=session_ids,
            include_global=include_global,
            profile=profile,
            max_count=args.max_count,
        )
        current_candidates = _candidate_payloads(
            session,
            behavior_scores=current_scores,
            idf_by_tag=scoped_clusters.idf_by_tag,
            df_by_tag=scoped_clusters.df_by_tag,
            cluster_count=len(scoped_clusters.clusters),
            query_session_id=window[0].session_id,
        )
        idf_candidates = _candidate_payloads(
            session,
            behavior_scores=idf_scores,
            idf_by_tag=scoped_clusters.idf_by_tag,
            df_by_tag=scoped_clusters.df_by_tag,
            cluster_count=len(scoped_clusters.clusters),
            query_session_id=window[0].session_id,
        )

    return {
        "index": index,
        "session_id": window[0].session_id,
        "chat_name": window[0].chat_name,
        "time_range": {
            "start": window[0].timestamp.isoformat(timespec="seconds"),
            "end": window[-1].timestamp.isoformat(timespec="seconds"),
        },
        "context": _message_payload(window),
        "profile": _profile_payload(profile),
        "raw_llm_response": raw_llm_response,
        "compare": _compare_candidate_sets(current_candidates, idf_candidates),
        "current": {
            "method": "current_tag_cluster_spread_1",
            "candidates": current_candidates,
        },
        "idf": {
            "method": "idf_tag_cluster_spread_1",
            "candidates": idf_candidates,
        },
    }


async def build_report(args: Namespace) -> dict[str, Any]:
    grouped_messages = _load_recent_messages(args.days)
    windows = _sample_windows(
        grouped_messages,
        sample_count=args.samples,
        window_size=args.window_size,
        seed=args.seed,
        max_gap_minutes=args.max_gap_minutes,
    )
    samples: list[dict[str, Any]] = []
    llm_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    for index, window in enumerate(windows, start=1):
        profile, raw_llm_response = await _analyze_profile_with_llm(window, client=llm_client)
        if profile is None:
            continue
        samples.append(
            _build_sample_result(
                index=index,
                window=window,
                profile=profile,
                raw_llm_response=raw_llm_response,
                args=args,
            )
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": args.days,
        "requested_samples": args.samples,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "seed": args.seed,
        "scope": "all_sessions" if args.all_sessions else "sample_session_only",
        "include_global": bool(args.all_sessions or args.include_global),
        "profile_source": "online_llm_behavior_scene_analyzer",
        "summary": _summarize_samples(samples),
        "samples": samples,
    }


def _summarize_samples(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "top1_changed_count": 0,
            "avg_jaccard": 0.0,
            "current_avg_top_df_ratio": 0.0,
            "idf_avg_top_df_ratio": 0.0,
        }
    compare_items = [sample["compare"] for sample in samples]
    return {
        "top1_changed_count": sum(1 for item in compare_items if item.get("top1_changed")),
        "top1_changed_rate": round(
            sum(1 for item in compare_items if item.get("top1_changed")) / float(len(compare_items)),
            4,
        ),
        "avg_jaccard": round(sum(float(item.get("jaccard") or 0.0) for item in compare_items) / len(compare_items), 4),
        "current_avg_top_df_ratio": round(
            sum(float(item.get("a_avg_top_df_ratio") or 0.0) for item in compare_items) / len(compare_items),
            4,
        ),
        "idf_avg_top_df_ratio": round(
            sum(float(item.get("b_avg_top_df_ratio") or 0.0) for item in compare_items) / len(compare_items),
            4,
        ),
    }


def _candidate_line(candidate: dict[str, Any]) -> str:
    return (
        f"- #{candidate.get('id')} score={candidate.get('retrieval_score')} "
        f"cluster=#{candidate.get('scene_cluster_id')} df={candidate.get('scene_avg_df_ratio')} "
        f"行为：{candidate.get('action')}；结果：{candidate.get('outcome')}\n"
        f"  场景：{candidate.get('scene_tags')}"
    )


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    summary = report["summary"]
    lines.append("# 行为检索 IDF AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 抽样范围：近 {report['days']} 天")
    lines.append(f"- 样本数：{report['sample_count']} / 请求 {report['requested_samples']}")
    lines.append(f"- 窗口大小：{report['window_size']}")
    lines.append(f"- seed：`{report['seed']}`")
    lines.append(f"- 范围：{report['scope']}，include_global={report['include_global']}")
    lines.append("- A：当前 `tag_cluster_spread_1`")
    lines.append("- B：实验 `idf_tag_cluster_spread_1`")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- Top1 变化：{summary['top1_changed_count']} / {report['sample_count']}")
    lines.append(f"- Top1 变化率：{summary['top1_changed_rate']}")
    lines.append(f"- TopK 平均 Jaccard：{summary['avg_jaccard']}")
    lines.append(f"- A Top3 场景平均 df_ratio：{summary['current_avg_top_df_ratio']}")
    lines.append(f"- B Top3 场景平均 df_ratio：{summary['idf_avg_top_df_ratio']}")
    lines.append("")

    for sample in report["samples"]:
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间范围：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append(
            f"- Top1 变化：{sample['compare']['top1_changed']} "
            f"A=#{sample['compare']['a_top1']} B=#{sample['compare']['b_top1']}"
        )
        lines.append(f"- TopK Jaccard：{sample['compare']['jaccard']}")
        lines.append("")
        lines.append("### 上下文")
        for message in sample["context"]:
            lines.append(f"- `{message['time']}` **{message['speaker']}**：{message['text']}")
        lines.append("")
        lines.append("### 场景画像")
        profile = sample["profile"]
        lines.append(f"- summary：{profile['summary']}")
        lines.append(f"- tag_key：`{profile['tag_key']}`")
        lines.append("")
        lines.append("### A 当前检索")
        if not sample["current"]["candidates"]:
            lines.append("- 无命中")
        for candidate in sample["current"]["candidates"][:DEFAULT_TOP_COUNT]:
            lines.append(_candidate_line(candidate))
        lines.append("")
        lines.append("### B IDF 检索")
        if not sample["idf"]["candidates"]:
            lines.append("- 无命中")
        for candidate in sample["idf"]["candidates"][:DEFAULT_TOP_COUNT]:
            lines.append(_candidate_line(candidate))
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="从主库随机抽取聊天窗口，对比当前行为检索与 IDF 加权检索。")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--window-size", type=int, default=8)
    parser.add_argument("--max-gap-minutes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--max-count", type=int, default=8)
    parser.add_argument("--include-global", action="store_true")
    parser.add_argument("--all-sessions", action="store_true", help="检索所有 session_id 的行为数据。")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def main() -> None:
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

from argparse import ArgumentParser, Namespace
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Any, Iterable, Optional

import asyncio
import json
import sys
import types

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.offline_behavior_learning import (  # noqa: E402
    _build_engine,
    _build_session_provider,
    _initialize_target_database,
    _patch_behavior_model_name,
    _patch_behavior_storage,
    _resolve_path,
    _window_hash,
)
from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorExperiencePath,
    BehaviorSceneCluster,
    BehaviorSceneTagCluster,
    ChatSession,
    Messages,
)
from src.learners.behavior_learner import BehaviorLearner  # noqa: E402
from src.learners.behavior_pattern_store import behavior_pattern_to_dict  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    format_scene_cluster_distribution,
    retrieve_behavior_scores_from_scene_clusters,
)

RETRIEVAL_METHODS = {
    "direct": "direct_domain_overlap",
    "spread1": "tag_cluster_spread_1",
    "spread2": "tag_cluster_spread_2",
}


@dataclass(frozen=True)
class EvalWindow:
    session_id: str
    chat_name: str
    messages: list[SessionMessage]


def _chat_display_name(chat_session: Optional[ChatSession], session_id: str) -> str:
    if chat_session is None:
        return session_id
    if chat_session.group_name:
        return chat_session.group_name
    if chat_session.user_nickname:
        return f"{chat_session.user_nickname} 的私聊"
    return chat_session.session_id


def _load_trained_message_ids(progress_path: Path) -> set[str]:
    if not progress_path.exists():
        return set()

    trained_message_ids: set[str] = set()
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("status") not in {"started", "completed"}:
            continue
        for message_id in payload.get("message_ids") or []:
            normalized_id = str(message_id or "").strip()
            if normalized_id:
                trained_message_ids.add(normalized_id)
    return trained_message_ids


def _requested_session_ids(args: Namespace) -> list[str]:
    session_ids: list[str] = []
    for raw_value in args.chat_id:
        for item in str(raw_value or "").replace("，", ",").split(","):
            session_id = item.strip()
            if session_id and session_id not in session_ids:
                session_ids.append(session_id)
    return session_ids


def _discover_session_ids(source_session: Session, *, min_messages: int) -> list[str]:
    statement = (
        select(Messages.session_id, func.count(Messages.id))
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .group_by(Messages.session_id)
        .having(func.count(Messages.id) >= min_messages)
    )
    return [
        str(row[0])
        for row in source_session.exec(statement).all()
        if str(row[0] or "").strip()
    ]


def _discover_learned_session_ids(target_session: Session) -> list[str]:
    statement = (
        select(BehaviorExperiencePath.session_id, func.count(BehaviorExperiencePath.id))
        .where(BehaviorExperiencePath.enabled.is_(True))  # type: ignore[attr-defined]
        .where(BehaviorExperiencePath.session_id.is_not(None))  # type: ignore[attr-defined]
        .group_by(BehaviorExperiencePath.session_id)
        .order_by(func.count(BehaviorExperiencePath.id).desc())
    )
    return [
        str(row[0])
        for row in target_session.exec(statement).all()
        if str(row[0] or "").strip()
    ]


def _load_messages_by_session(
    source_session: Session,
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
    for record in source_session.exec(statement).all():
        text = " ".join((record.processed_plain_text or "").split()).strip()
        if len(text) < min_text_length:
            continue
        messages.append(SessionMessage.from_db_instance(record))
    return messages


def _iter_non_overlapping_windows(
    messages: list[SessionMessage],
    *,
    window_size: int,
) -> Iterable[list[SessionMessage]]:
    for start_index in range(0, len(messages), window_size):
        window = messages[start_index : start_index + window_size]
        if len(window) < window_size:
            break
        yield window


def _build_eval_windows(
    source_session: Session,
    *,
    session_ids: list[str],
    trained_message_ids: set[str],
    window_size: int,
    min_text_length: int,
    limit: int,
    seed: int,
    samples: int,
    balanced_by_chat: bool,
) -> list[EvalWindow]:
    randomizer = Random(seed)
    windows_by_session: dict[str, list[EvalWindow]] = {}

    for session_id in session_ids:
        chat = source_session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
        chat_name = _chat_display_name(chat, session_id)
        messages = _load_messages_by_session(
            source_session,
            session_id=session_id,
            min_text_length=min_text_length,
            limit=limit,
        )
        windows: list[EvalWindow] = []
        for window in _iter_non_overlapping_windows(messages, window_size=window_size):
            message_ids = {str(message.message_id or "").strip() for message in window}
            if message_ids & trained_message_ids:
                continue
            windows.append(EvalWindow(session_id=session_id, chat_name=chat_name, messages=window))
        randomizer.shuffle(windows)
        windows_by_session[session_id] = windows

    if balanced_by_chat:
        selected: list[EvalWindow] = []
        session_order = list(windows_by_session)
        randomizer.shuffle(session_order)
        while len(selected) < samples:
            added = False
            for session_id in session_order:
                windows = windows_by_session[session_id]
                if not windows:
                    continue
                selected.append(windows.pop())
                added = True
                if len(selected) >= samples:
                    break
            if not added:
                break
        return selected

    all_windows = [window for windows in windows_by_session.values() for window in windows]
    randomizer.shuffle(all_windows)
    return all_windows[:samples]


def _profile_payload(profile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "tag_clusters": profile.domain_prompt_payloads(),
        "need": profile.need_prompt_payload(),
        "other_traits": profile.other_traits_prompt_payloads(),
        "confidence": profile.confidence,
    }


def _scene_cluster_tag_summary(scene_cluster_id: Any) -> str:
    if scene_cluster_id is None:
        return ""
    try:
        normalized_cluster_id = int(scene_cluster_id)
    except (TypeError, ValueError):
        return ""

    import src.learners.behavior_scene_cluster_store as behavior_scene_cluster_store

    try:
        with behavior_scene_cluster_store.get_db_session(auto_commit=False) as session:
            cluster = session.get(BehaviorSceneCluster, normalized_cluster_id)
            if cluster is None:
                return ""
            try:
                distribution = json.loads(cluster.tag_distribution or "[]")
            except (TypeError, ValueError):
                distribution = []
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
            cluster_keys = {cluster_key for _, cluster_key, _ in tag_entries}
            if not cluster_keys:
                return format_scene_cluster_distribution(distribution)
            tag_rows = session.exec(
                select(BehaviorSceneTagCluster).where(BehaviorSceneTagCluster.cluster_key.in_(cluster_keys))  # type: ignore[attr-defined]
            ).all()
            tags_by_key: dict[tuple[str, str], list[str]] = {}
            for row in tag_rows:
                if not row.tag_kind or not row.cluster_key or not row.tag:
                    continue
                key = (row.tag_kind, row.cluster_key)
                tags_by_key.setdefault(key, [])
                if row.tag not in tags_by_key[key]:
                    tags_by_key[key].append(row.tag)
            parts: list[str] = []
            for tag_kind, cluster_key, probability in sorted(tag_entries, key=lambda item: item[2], reverse=True)[:8]:
                labels = tags_by_key.get((tag_kind, cluster_key), [])
                readable = "/".join(labels[:3]) if labels else cluster_key
                parts.append(f"{tag_kind}:{readable}={probability:.3f}")
            return "；".join(parts)
    except Exception:
        return ""


def _retrieve_candidates(
    *,
    session_id: str,
    profile,
    max_count: int,
    include_global: bool,
    retrieval_mode: str,
) -> list[dict[str, Any]]:
    behavior_scores = retrieve_behavior_scores_from_scene_clusters(
        session_ids={session_id},
        include_global=include_global,
        profile=profile,
        max_count=max_count,
        retrieval_mode=retrieval_mode,
    )
    candidates: list[dict[str, Any]] = []
    for behavior_id, score in behavior_scores.items():
        candidate = behavior_pattern_to_dict(type("PathRef", (), {"id": behavior_id})())
        if not candidate:
            continue
        candidate["scene_tags"] = _scene_cluster_tag_summary(candidate.get("scene_cluster_id"))
        candidate["retrieval_score"] = round(float(score or 0.0), 4)
        candidates.append(candidate)
    return candidates


def _brief_candidate(candidate: dict[str, Any], *, query_session_id: str) -> dict[str, Any]:
    candidate_session_id = candidate.get("session_id")
    return {
        "id": candidate.get("id"),
        "score": candidate.get("retrieval_score", candidate.get("score")),
        "session_id": candidate.get("session_id"),
        "same_chat": candidate_session_id == query_session_id,
        "scene_tags": candidate.get("scene_tags"),
        "scene_cluster_id": candidate.get("scene_cluster_id"),
        "action": candidate.get("action"),
        "outcome": candidate.get("outcome"),
        "actor_type": candidate.get("actor_type"),
        "learning_type": candidate.get("learning_type"),
        "count": candidate.get("count"),
        "path_score": candidate.get("score"),
    }


def _analyze_segment_retrieval(retrieval_results: dict[str, list[dict[str, Any]]]) -> list[str]:
    same_chat_candidates = retrieval_results.get("same_chat", [])
    all_chat_candidates = retrieval_results.get("all_chats", [])
    lines: list[str] = []

    if "same_chat" in retrieval_results and "all_chats" in retrieval_results:
        if not same_chat_candidates and not all_chat_candidates:
            return ["同 chat_id 与全库范围均无命中，当前场景画像没有召回到可用行为路径。"]
        if not same_chat_candidates and all_chat_candidates:
            top = all_chat_candidates[0]
            source = "同 chat" if top.get("same_chat") else "其他 chat"
            return [
                f"同 chat_id 无命中；全库召回 {len(all_chat_candidates)} 条，Top1 来自{source}，说明跨聊天流存在可复用经验。"
            ]
        if same_chat_candidates and not all_chat_candidates:
            return ["同 chat_id 有命中但全库无命中，这通常表示当前图检索范围或过滤条件存在异常，需要进一步核查。"]

        same_top = same_chat_candidates[0]
        all_top = all_chat_candidates[0]
        same_ids = {candidate.get("id") for candidate in same_chat_candidates}
        all_ids = {candidate.get("id") for candidate in all_chat_candidates}
        overlap_count = len(same_ids & all_ids)
        same_score = float(same_top.get("score") or 0.0)
        all_score = float(all_top.get("score") or 0.0)

        if same_top.get("id") == all_top.get("id"):
            lines.append("全库 Top1 与同 chat_id Top1 相同，说明本聊天流自身经验已经是最强召回。")
        elif all_top.get("same_chat"):
            lines.append("全库 Top1 仍来自同 chat_id，但排序与同 chat_id 范围不同，需要关注图分数聚合差异。")
        else:
            lines.append("全库 Top1 来自其他 chat_id，说明跨聊天流经验在当前场景下比本聊天流经验更强。")

        if all_score > same_score:
            lines.append(f"全库 Top1 分数高于同 chat_id Top1（{all_score:.4f} > {same_score:.4f}）。")
        elif all_score < same_score:
            lines.append(f"全库 Top1 分数低于同 chat_id Top1（{all_score:.4f} < {same_score:.4f}）。")
        else:
            lines.append(f"两种范围 Top1 分数相同（{same_score:.4f}）。")
        lines.append(f"Top{len(same_chat_candidates)} / Top{len(all_chat_candidates)} 候选重叠 {overlap_count} 条。")
        return lines

    for mode_key, candidates in retrieval_results.items():
        if not candidates:
            lines.append(f"{mode_key} 范围无命中。")
            continue
        top = candidates[0]
        source = "同 chat" if top.get("same_chat") else "其他 chat"
        lines.append(f"{mode_key} 范围召回 {len(candidates)} 条，Top1 来自{source}，分数 {top.get('score')}。")
    return lines


def _build_report_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    mode_stats: dict[str, dict[str, Any]] = {}
    mode_order = {"same_chat": 0, "all_chats": 1}
    modes = sorted(
        {
            mode
            for sample in samples
            for segment in sample["segments"]
            for mode in segment.get("retrieval", {})
        },
        key=lambda mode: mode_order.get(mode, 99),
    )

    for mode in modes:
        segment_count = 0
        hit_segments = 0
        total_candidates = 0
        top_scores: list[float] = []
        top1_same_chat_count = 0
        top1_cross_chat_count = 0
        unique_candidate_ids: set[int] = set()
        top1_session_counter: Counter[str] = Counter()

        for sample in samples:
            for segment in sample["segments"]:
                if mode not in segment.get("retrieval", {}):
                    continue
                segment_count += 1
                candidates = segment["retrieval"][mode]
                total_candidates += len(candidates)
                for candidate in candidates:
                    candidate_id = candidate.get("id")
                    if candidate_id is not None:
                        unique_candidate_ids.add(int(candidate_id))
                if not candidates:
                    continue
                hit_segments += 1
                top = candidates[0]
                top_scores.append(float(top.get("score") or 0.0))
                if top.get("same_chat"):
                    top1_same_chat_count += 1
                else:
                    top1_cross_chat_count += 1
                top1_session_counter[str(top.get("session_id") or "")] += 1

        hit_rate = hit_segments / segment_count if segment_count else 0.0
        avg_candidate_count = total_candidates / segment_count if segment_count else 0.0
        avg_top_score = sum(top_scores) / len(top_scores) if top_scores else 0.0
        mode_stats[mode] = {
            "segment_count": segment_count,
            "hit_segments": hit_segments,
            "miss_segments": segment_count - hit_segments,
            "hit_rate": round(hit_rate, 4),
            "avg_candidate_count": round(avg_candidate_count, 4),
            "avg_top_score": round(avg_top_score, 4),
            "top1_same_chat_count": top1_same_chat_count,
            "top1_cross_chat_count": top1_cross_chat_count,
            "unique_candidate_count": len(unique_candidate_ids),
            "top1_session_distribution": dict(top1_session_counter.most_common(20)),
        }

    comparison = {
        "both_mode_segments": 0,
        "same_empty_all_hit": 0,
        "same_hit_all_empty": 0,
        "same_and_all_hit": 0,
        "same_and_all_same_top1": 0,
        "same_and_all_different_top1": 0,
        "all_top1_cross_chat": 0,
        "avg_topk_overlap": 0.0,
    }
    overlap_values: list[int] = []
    for sample in samples:
        for segment in sample["segments"]:
            retrieval = segment.get("retrieval", {})
            if "same_chat" not in retrieval or "all_chats" not in retrieval:
                continue
            comparison["both_mode_segments"] += 1
            same_candidates = retrieval["same_chat"]
            all_candidates = retrieval["all_chats"]
            if not same_candidates and all_candidates:
                comparison["same_empty_all_hit"] += 1
            if same_candidates and not all_candidates:
                comparison["same_hit_all_empty"] += 1
            if same_candidates and all_candidates:
                comparison["same_and_all_hit"] += 1
                if same_candidates[0].get("id") == all_candidates[0].get("id"):
                    comparison["same_and_all_same_top1"] += 1
                else:
                    comparison["same_and_all_different_top1"] += 1
            if all_candidates and not all_candidates[0].get("same_chat"):
                comparison["all_top1_cross_chat"] += 1
            same_ids = {candidate.get("id") for candidate in same_candidates}
            all_ids = {candidate.get("id") for candidate in all_candidates}
            overlap_values.append(len(same_ids & all_ids))
    if overlap_values:
        comparison["avg_topk_overlap"] = round(sum(overlap_values) / len(overlap_values), 4)

    method_comparison: dict[str, dict[str, Any]] = {}
    for scope_name in ("same_chat", "all_chats"):
        direct_key = f"{scope_name}/direct"
        method_summary: dict[str, Any] = {
            "segment_count": 0,
            "direct_hit": 0,
            "spread1_hit": 0,
            "spread2_hit": 0,
            "spread1_new_hit": 0,
            "spread2_new_hit": 0,
            "spread1_same_top1_as_direct": 0,
            "spread2_same_top1_as_direct": 0,
            "spread1_different_top1_from_direct": 0,
            "spread2_different_top1_from_direct": 0,
            "spread1_avg_topk_overlap_with_direct": 0.0,
            "spread2_avg_topk_overlap_with_direct": 0.0,
        }
        spread1_overlap_values: list[int] = []
        spread2_overlap_values: list[int] = []
        for sample in samples:
            for segment in sample["segments"]:
                retrieval = segment.get("retrieval", {})
                if direct_key not in retrieval:
                    continue
                method_summary["segment_count"] += 1
                direct_candidates = retrieval.get(direct_key, [])
                spread1_candidates = retrieval.get(f"{scope_name}/spread1", [])
                spread2_candidates = retrieval.get(f"{scope_name}/spread2", [])
                if direct_candidates:
                    method_summary["direct_hit"] += 1
                if spread1_candidates:
                    method_summary["spread1_hit"] += 1
                if spread2_candidates:
                    method_summary["spread2_hit"] += 1
                if not direct_candidates and spread1_candidates:
                    method_summary["spread1_new_hit"] += 1
                if not direct_candidates and spread2_candidates:
                    method_summary["spread2_new_hit"] += 1
                if direct_candidates and spread1_candidates:
                    if direct_candidates[0].get("id") == spread1_candidates[0].get("id"):
                        method_summary["spread1_same_top1_as_direct"] += 1
                    else:
                        method_summary["spread1_different_top1_from_direct"] += 1
                if direct_candidates and spread2_candidates:
                    if direct_candidates[0].get("id") == spread2_candidates[0].get("id"):
                        method_summary["spread2_same_top1_as_direct"] += 1
                    else:
                        method_summary["spread2_different_top1_from_direct"] += 1
                direct_ids = {candidate.get("id") for candidate in direct_candidates}
                spread1_overlap_values.append(
                    len(direct_ids & {candidate.get("id") for candidate in spread1_candidates})
                )
                spread2_overlap_values.append(
                    len(direct_ids & {candidate.get("id") for candidate in spread2_candidates})
                )
        if method_summary["segment_count"] > 0:
            method_summary["direct_hit_rate"] = round(
                method_summary["direct_hit"] / method_summary["segment_count"],
                4,
            )
            method_summary["spread1_hit_rate"] = round(
                method_summary["spread1_hit"] / method_summary["segment_count"],
                4,
            )
            method_summary["spread2_hit_rate"] = round(
                method_summary["spread2_hit"] / method_summary["segment_count"],
                4,
            )
        if spread1_overlap_values:
            method_summary["spread1_avg_topk_overlap_with_direct"] = round(
                sum(spread1_overlap_values) / len(spread1_overlap_values),
                4,
            )
        if spread2_overlap_values:
            method_summary["spread2_avg_topk_overlap_with_direct"] = round(
                sum(spread2_overlap_values) / len(spread2_overlap_values),
                4,
            )
        method_comparison[scope_name] = method_summary

    sample_distribution = Counter(str(sample["session_id"]) for sample in samples)
    return {
        "modes": mode_stats,
        "comparison": comparison,
        "method_comparison": method_comparison,
        "sample_session_distribution": dict(sample_distribution.most_common()),
    }


async def _evaluate_window(window: EvalWindow, args: Namespace) -> dict[str, Any]:
    learner = BehaviorLearner(window.session_id)

    def resolve_learning_session_id(self, pending_messages: list[SessionMessage]) -> Optional[str]:
        return window.session_id

    learner._resolve_learning_session_id = types.MethodType(resolve_learning_session_id, learner)  # type: ignore[method-assign]
    segments = await learner._analyze_learning_scene_segments(
        window.messages,
        learning_session_id=window.session_id,
    )

    segment_results: list[dict[str, Any]] = []
    for segment in segments:
        retrieval_results: dict[str, list[dict[str, Any]]] = {}
        for method_name, retrieval_mode in RETRIEVAL_METHODS.items():
            if args.scope in {"both", "same-chat"}:
                same_chat_candidates = _retrieve_candidates(
                    session_id=window.session_id,
                    profile=segment.profile,
                    max_count=args.max_count,
                    include_global=False,
                    retrieval_mode=retrieval_mode,
                )
                retrieval_results[f"same_chat/{method_name}"] = [
                    _brief_candidate(candidate, query_session_id=window.session_id)
                    for candidate in same_chat_candidates
                ]
            if args.scope in {"both", "all-chats"}:
                all_chat_candidates = _retrieve_candidates(
                    session_id=window.session_id,
                    profile=segment.profile,
                    max_count=args.max_count,
                    include_global=True,
                    retrieval_mode=retrieval_mode,
                )
                retrieval_results[f"all_chats/{method_name}"] = [
                    _brief_candidate(candidate, query_session_id=window.session_id)
                    for candidate in all_chat_candidates
                ]
        segment_results.append(
            {
                "segment_id": segment.segment_id,
                "title": segment.title,
                "source_ids": segment.source_ids,
                "profile": _profile_payload(segment.profile),
                "retrieval": retrieval_results,
                "analysis": _analyze_segment_retrieval(retrieval_results),
            }
        )

    return {
        "session_id": window.session_id,
        "chat_name": window.chat_name,
        "window_hash": _window_hash(window.messages),
        "message_count": len(window.messages),
        "time_range": {
            "start": window.messages[0].timestamp.isoformat(timespec="seconds"),
            "end": window.messages[-1].timestamp.isoformat(timespec="seconds"),
        },
        "context": [
            {
                "message_id": message.message_id,
                "time": message.timestamp.isoformat(timespec="seconds"),
                "speaker": message.message_info.user_info.user_cardname
                or message.message_info.user_info.user_nickname
                or message.message_info.user_info.user_id,
                "text": " ".join((message.processed_plain_text or "").split()).strip(),
            }
            for message in window.messages
        ],
        "segments": segment_results,
    }


async def build_report(args: Namespace) -> dict[str, Any]:
    source_db_path = _resolve_path(args.source_db)
    target_db_path = _resolve_path(args.target_db)
    progress_path = _resolve_path(args.progress_jsonl)
    source_engine = _build_engine(source_db_path, readonly=True)
    target_engine = create_engine(
        f"sqlite:///{target_db_path.as_posix()}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    _initialize_target_database(target_engine)

    target_session_local = sessionmaker(autocommit=False, autoflush=False, bind=target_engine, class_=Session)
    get_target_session = _build_session_provider(target_session_local)
    _patch_behavior_storage(get_target_session)
    _patch_behavior_model_name(args.model_name)

    trained_message_ids = _load_trained_message_ids(progress_path)
    source_session_local = sessionmaker(autocommit=False, autoflush=False, bind=source_engine, class_=Session)
    with target_session_local() as target_session:
        learned_session_ids = _discover_learned_session_ids(target_session)

    with source_session_local() as source_session:
        session_ids = _requested_session_ids(args)
        if not session_ids:
            if args.sample_from == "learned":
                session_ids = learned_session_ids
            else:
                session_ids = _discover_session_ids(source_session, min_messages=args.min_chat_messages)
        if not session_ids:
            raise ValueError("没有找到可评测的 chat_id，请先学习行为路径或使用 --sample-from source / --chat-id 指定")
        windows = _build_eval_windows(
            source_session,
            session_ids=session_ids,
            trained_message_ids=trained_message_ids,
            window_size=args.window_size,
            min_text_length=args.min_text_length,
            limit=args.limit,
            seed=args.seed,
            samples=args.samples,
            balanced_by_chat=args.balanced_by_chat,
        )
    if not windows:
        raise ValueError("没有抽到未训练评测窗口，可能是窗口都已被 progress JSONL 排除或消息不足")

    samples: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        print(f"[{index}/{len(windows)}] 评测窗口: {window.chat_name} {window.messages[0].timestamp} ~ {window.messages[-1].timestamp}")
        sample = await _evaluate_window(window, args)
        sample["index"] = index
        samples.append(sample)

    stats = _build_report_stats(samples)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_db": str(source_db_path),
        "target_db": str(target_db_path),
        "progress_jsonl": str(progress_path),
        "trained_message_id_count": len(trained_message_ids),
        "sample_from": args.sample_from,
        "scope": args.scope,
        "candidate_pool_session_count": len(session_ids),
        "candidate_pool_session_ids": session_ids,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "max_count": args.max_count,
        "model_name": args.model_name or "配置任务 learner 默认模型",
        "stats": stats,
        "samples": samples,
    }


def _mode_label(mode: str) -> str:
    labels = {
        "same_chat": "同 chat_id",
        "all_chats": "所有 chat_id",
        "same_chat/direct": "同 chat_id / 无扩散",
        "same_chat/spread1": "同 chat_id / 一次扩散",
        "same_chat/spread2": "同 chat_id / 二次扩散",
        "all_chats/direct": "所有 chat_id / 无扩散",
        "all_chats/spread1": "所有 chat_id / 一次扩散",
        "all_chats/spread2": "所有 chat_id / 二次扩散",
    }
    return labels.get(mode, mode)


def _candidate_markdown_lines(candidate: dict[str, Any], rank: int) -> list[str]:
    session_scope = "同 chat" if candidate.get("same_chat") else "其他 chat"
    return [
        (
            f"- Top{rank} #{candidate.get('id')} score={candidate.get('score')} "
            f"path_score={candidate.get('path_score')} session={candidate.get('session_id')} "
            f"({session_scope}) cluster=#{candidate.get('scene_cluster_id')}"
        ),
        f"  - 场景标签：{candidate.get('scene_tags') or ''}",
        f"  - 行为：{candidate.get('action') or ''}",
        f"  - 结果：{candidate.get('outcome') or ''}",
        (
            f"  - 类型：actor={candidate.get('actor_type') or ''}, "
            f"learning={candidate.get('learning_type') or ''}, count={candidate.get('count')}"
        ),
    ]


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = [
        "# 离线行为检索评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 样本数：{report['sample_count']}",
        f"- 窗口大小：{report['window_size']}",
        f"- TopK：{report['max_count']}",
        f"- 模型：{report['model_name']}",
        f"- 已排除训练消息数：{report['trained_message_id_count']}",
        f"- 抽样来源：{report['sample_from']}",
        f"- 召回范围：{report['scope']}",
        f"- 候选 chat_id 数：{report['candidate_pool_session_count']}",
        "",
        "## 总体指标",
        "",
    ]
    stats = report.get("stats", {})
    for mode, mode_stats in stats.get("modes", {}).items():
        lines.append(f"### {_mode_label(mode)}")
        lines.append("")
        lines.append(f"- 场景片段数：{mode_stats['segment_count']}")
        lines.append(f"- 命中片段数：{mode_stats['hit_segments']}")
        lines.append(f"- 未命中片段数：{mode_stats['miss_segments']}")
        lines.append(f"- 命中率：{mode_stats['hit_rate']:.2%}")
        lines.append(f"- 平均候选数：{mode_stats['avg_candidate_count']}")
        lines.append(f"- 平均 Top1 分数：{mode_stats['avg_top_score']}")
        lines.append(f"- Top1 来自同 chat_id：{mode_stats['top1_same_chat_count']}")
        lines.append(f"- Top1 来自其他 chat_id：{mode_stats['top1_cross_chat_count']}")
        lines.append(f"- 唯一候选路径数：{mode_stats['unique_candidate_count']}")
        lines.append("")

    comparison = stats.get("comparison", {})
    if comparison:
        lines.extend(
            [
                "### 双范围对照",
                "",
                f"- 同时具备两种召回范围的片段数：{comparison['both_mode_segments']}",
                f"- 同 chat_id 空、全库命中的片段数：{comparison['same_empty_all_hit']}",
                f"- 同 chat_id 命中、全库空的片段数：{comparison['same_hit_all_empty']}",
                f"- 两边都命中的片段数：{comparison['same_and_all_hit']}",
                f"- 两边 Top1 相同的片段数：{comparison['same_and_all_same_top1']}",
                f"- 两边 Top1 不同的片段数：{comparison['same_and_all_different_top1']}",
                f"- 全库 Top1 来自其他 chat_id 的片段数：{comparison['all_top1_cross_chat']}",
                f"- 平均 TopK 重叠数：{comparison['avg_topk_overlap']}",
                "",
            ]
        )

    method_comparison = stats.get("method_comparison", {})
    if method_comparison:
        lines.extend(["### 三种检索方法对照", ""])
        for scope_name, summary in method_comparison.items():
            lines.append(f"#### {_mode_label(scope_name)}")
            lines.append("")
            lines.append(f"- 片段数：{summary['segment_count']}")
            lines.append(f"- 无扩散命中：{summary['direct_hit']}（{summary.get('direct_hit_rate', 0):.2%}）")
            lines.append(f"- 一次扩散命中：{summary['spread1_hit']}（{summary.get('spread1_hit_rate', 0):.2%}）")
            lines.append(f"- 二次扩散命中：{summary['spread2_hit']}（{summary.get('spread2_hit_rate', 0):.2%}）")
            lines.append(f"- 一次扩散新增命中：{summary['spread1_new_hit']}")
            lines.append(f"- 二次扩散新增命中：{summary['spread2_new_hit']}")
            lines.append(f"- 一次扩散 Top1 与无扩散相同：{summary['spread1_same_top1_as_direct']}")
            lines.append(f"- 一次扩散 Top1 与无扩散不同：{summary['spread1_different_top1_from_direct']}")
            lines.append(f"- 二次扩散 Top1 与无扩散相同：{summary['spread2_same_top1_as_direct']}")
            lines.append(f"- 二次扩散 Top1 与无扩散不同：{summary['spread2_different_top1_from_direct']}")
            lines.append(f"- 一次扩散与无扩散平均 TopK 重叠：{summary['spread1_avg_topk_overlap_with_direct']}")
            lines.append(f"- 二次扩散与无扩散平均 TopK 重叠：{summary['spread2_avg_topk_overlap_with_direct']}")
            lines.append("")

    lines.extend(["## 逐条召回分析", ""])
    for sample in report["samples"]:
        lines.append(f"### 样本 {sample['index']} - {sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append(f"- window_hash：`{sample['window_hash']}`")
        lines.append("")
        for segment in sample["segments"]:
            lines.append(f"#### 片段 {segment['segment_id']}：{segment['title']}")
            lines.append("")
            lines.append(f"- source_ids：{', '.join(segment['source_ids'])}")
            lines.append(f"- summary：{segment['profile']['summary']}")
            lines.append("")
            lines.append("分析：")
            for item in segment.get("analysis", []):
                lines.append(f"- {item}")
            lines.append("")
            for mode, candidates in segment.get("retrieval", {}).items():
                lines.append(f"{_mode_label(mode)} Top 候选：")
                if not candidates:
                    lines.append("- 无命中")
                for rank, candidate in enumerate(candidates, start=1):
                    lines.extend(_candidate_markdown_lines(candidate, rank))
                lines.append("")
            lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="从未训练窗口抽样，按 Maisaka 场景概括流程评测离线行为库检索。")
    parser.add_argument("--source-db", default="data/MaiBot.db")
    parser.add_argument("--target-db", default="data/behaviro_learn_test/offline_behavior_learning.db")
    parser.add_argument("--progress-jsonl", default="data/behaviro_learn_test/offline_behavior_learning_progress.jsonl")
    parser.add_argument("--chat-id", action="append", default=[], help="限定 chat_id，可重复传入或逗号分隔。")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--window-size", type=int, default=40)
    parser.add_argument("--max-count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260611)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=1)
    parser.add_argument("--min-chat-messages", type=int, default=200)
    parser.add_argument("--balanced-by-chat", action="store_true")
    parser.add_argument(
        "--sample-from",
        choices=["learned", "source"],
        default="learned",
        help="未指定 --chat-id 时的抽样来源：learned=离线库已有行为路径的 chat_id，source=源库所有满足消息数限制的 chat_id。",
    )
    parser.add_argument(
        "--scope",
        choices=["both", "same-chat", "all-chats"],
        default="both",
        help="召回范围：both 同时评测同 chat_id 与所有 chat_id。",
    )
    parser.add_argument("--include-global", action="store_true", help="兼容旧参数，等价于 --scope all-chats。")
    parser.add_argument("--model-name", default="")
    parser.add_argument("--output", default="data/behaviro_learn_test/offline_behavior_retrieval_eval.md")
    parser.add_argument("--json-output", default="data/behaviro_learn_test/offline_behavior_retrieval_eval.json")
    args = parser.parse_args()
    if args.include_global:
        args.scope = "all-chats"
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        args = parse_args()
        report = asyncio.run(build_report(args))
        output_path = _resolve_path(args.output)
        json_output_path = _resolve_path(args.json_output)
        write_markdown_report(report, output_path)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Markdown report: {output_path}")
        print(f"JSON report: {json_output_path}")
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

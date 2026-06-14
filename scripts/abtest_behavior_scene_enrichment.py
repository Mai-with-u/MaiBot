from argparse import ArgumentParser, Namespace
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import log
from pathlib import Path
from random import Random
from typing import Any, Sequence

import asyncio
import json
import sys

from sqlalchemy import func
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.offline_behavior_learning import _chat_display_name, _resolve_path  # noqa: E402
from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorExperiencePath,
    BehaviorSceneCluster,
    BehaviorSceneTagCluster,
    ChatSession,
    Messages,
)
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import BehaviorScenarioProfile, parse_behavior_scenario_segments_response  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    DIRECT_DOMAIN_OVERLAP_THRESHOLD,
    DIRECT_DOMAIN_OVERLAP_TOPK,
    SCENE_CLUSTER_REUSE_THRESHOLD,
    _distribution_to_mapping,
    _load_cluster_distribution,
    _load_tag_cluster_lookup,
    _scene_cluster_matches_sessions,
    build_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_scene_enrichment_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_scene_enrichment_abtest.json"
DEFAULT_WINDOW_SIZE = 30
DEFAULT_HALF_WINDOW = 15
DEFAULT_MAX_MESSAGE_GAP_MINUTES = 60
DEFAULT_MIN_WINDOW_MESSAGES = 8


@dataclass(frozen=True)
class SourceMessageWindow:
    session_id: str
    display_name: str
    current_messages: list[SessionMessage]
    previous_shift_messages: list[SessionMessage]
    next_shift_messages: list[SessionMessage]


@dataclass(frozen=True)
class ProfileRun:
    variant: str
    profile: BehaviorScenarioProfile
    raw_response: str
    distribution: dict[str, float]
    message_count: int
    time_range: dict[str, str]
    truncated: bool


@dataclass(frozen=True)
class ContinuityJudgement:
    is_continuous: bool
    continuity_score: float
    usable_variants: list[str]
    previous_to_center: str
    center_to_next: str
    reason: str
    raw_response: str


@dataclass(frozen=True)
class ScopedSceneData:
    clusters: list[BehaviorSceneCluster]
    tag_lookup: dict[tuple[str, str], str]
    df_by_tag: dict[str, int]
    idf_by_tag: dict[str, float]


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


def _build_candidate_windows_for_session(
    *,
    session_id: str,
    display_name: str,
    messages: list[SessionMessage],
    window_size: int,
    half_window: int,
    step: int,
) -> list[SourceMessageWindow]:
    windows: list[SourceMessageWindow] = []
    start_min = half_window
    start_max_exclusive = len(messages) - window_size - half_window + 1
    for center_start in range(start_min, max(start_min, start_max_exclusive), max(1, step)):
        previous_shift_messages = messages[center_start - half_window : center_start + half_window]
        current_messages = messages[center_start : center_start + window_size]
        next_shift_messages = messages[center_start + half_window : center_start + window_size + half_window]
        if (
            len(previous_shift_messages) == window_size
            and len(current_messages) == window_size
            and len(next_shift_messages) == window_size
        ):
            windows.append(
                SourceMessageWindow(
                    session_id=session_id,
                    display_name=display_name,
                    current_messages=current_messages,
                    previous_shift_messages=previous_shift_messages,
                    next_shift_messages=next_shift_messages,
                )
            )
    return windows


def _select_base_windows(args: Namespace) -> list[SourceMessageWindow]:
    randomizer = Random(args.seed)
    with get_db_session(auto_commit=False) as session:
        session_ids = _split_values([*args.session_id, *args.chat_id])
        if not session_ids:
            session_ids = _discover_session_ids(
                session,
                min_messages=args.window_size + args.half_window * 2,
            )
        candidates: list[SourceMessageWindow] = []
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
            candidates.extend(
                _build_candidate_windows_for_session(
                    session_id=session_id,
                    display_name=_chat_display_name(chat_session),
                    messages=messages,
                    window_size=args.window_size,
                    half_window=args.half_window,
                    step=args.step,
                )
            )
    randomizer.shuffle(candidates)
    return candidates[: args.samples]


async def _analyze_profile_with_llm(
    messages: Sequence[SessionMessage],
    *,
    client: LLMServiceClient,
    temperature: float,
) -> tuple[BehaviorScenarioProfile | None, str]:
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
    result = await client.generate_response_with_messages(
        lambda _client: request_messages,
        options=LLMGenerationOptions(temperature=temperature),
    )
    raw_response = result.response or ""
    segments = parse_behavior_scenario_segments_response(raw_response)
    if not segments:
        return None, raw_response
    profile = segments[0].profile
    if not profile.tag_clusters:
        return None, raw_response
    return profile, raw_response


def _extract_json_object(raw_response: str) -> dict[str, Any]:
    content = (raw_response or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start_index = content.find("{")
    end_index = content.rfind("}")
    if start_index < 0 or end_index <= start_index:
        return {}
    try:
        parsed = json.loads(content[start_index : end_index + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _float_in_range(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(number, 1.0))


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


async def _judge_window_continuity_with_llm(
    runs: Sequence[ProfileRun],
    *,
    client: LLMServiceClient,
    temperature: float,
) -> ContinuityJudgement:
    variants_text: list[str] = []
    known_variants = {run.variant for run in runs}
    for run in runs:
        variants_text.append(
            "\n".join(
                [
                    f"variant: {run.variant}",
                    f"time_range: {run.time_range.get('start', '')} ~ {run.time_range.get('end', '')}",
                    f"message_count: {run.message_count}",
                    f"summary: {run.profile.summary}",
                    f"tags: {run.profile.tag_cluster_text()}",
                ]
            )
        )

    prompt = (
        "你是一个聊天行为学习质检器。请判断三个滑动窗口是否描述同一个连续场景，"
        "而不是相邻但已经换话题的片段。重点关注：话题对象、问题/目标、参与者互动目的、"
        "时间顺承关系是否一致。泛用氛围标签如玩梗、调侃、表情包不能单独证明连续。\n\n"
        "请只输出 JSON：\n"
        "{\n"
        '  "is_continuous": true/false,\n'
        '  "continuity_score": 0.0-1.0,\n'
        '  "usable_variants": ["previous15_current_first15", "current30", "current_last15_next15"],\n'
        '  "previous_to_center": "顺承/弱相关/断裂",\n'
        '  "center_to_next": "顺承/弱相关/断裂",\n'
        '  "reason": "一句话说明"\n'
        "}\n"
    )
    request_messages = [
        MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build(),
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content("\n\n---\n\n".join(variants_text) + "\n\n请输出 JSON。")
        .build(),
    ]
    result = await client.generate_response_with_messages(
        lambda _client: request_messages,
        options=LLMGenerationOptions(temperature=temperature),
    )
    raw_response = result.response or ""
    parsed = _extract_json_object(raw_response)
    usable_variants = [variant for variant in _list_of_strings(parsed.get("usable_variants")) if variant in known_variants]
    if not usable_variants and runs:
        usable_variants = ["current30"] if "current30" in known_variants else [runs[0].variant]

    return ContinuityJudgement(
        is_continuous=bool(parsed.get("is_continuous", False)),
        continuity_score=_float_in_range(parsed.get("continuity_score"), default=0.0),
        usable_variants=usable_variants,
        previous_to_center=str(parsed.get("previous_to_center") or ""),
        center_to_next=str(parsed.get("center_to_next") or ""),
        reason=str(parsed.get("reason") or ""),
        raw_response=raw_response,
    )


def _profile_distribution(profile: BehaviorScenarioProfile, *, tag_lookup: dict[tuple[str, str], str]) -> dict[str, float]:
    return _distribution_to_mapping(build_scene_cluster_distribution(profile, tag_lookup=tag_lookup), tag_lookup=tag_lookup)


def _normalize_distribution(distribution: dict[str, float]) -> dict[str, float]:
    total_probability = sum(max(float(probability or 0.0), 0.0) for probability in distribution.values())
    if total_probability <= 0:
        return {}
    return {
        tag: max(float(probability or 0.0), 0.0) / total_probability
        for tag, probability in sorted(distribution.items())
        if probability > 0
    }


def _average_distributions(distributions: Sequence[dict[str, float]]) -> dict[str, float]:
    if not distributions:
        return {}
    merged: dict[str, float] = defaultdict(float)
    for distribution in distributions:
        for tag, probability in distribution.items():
            merged[tag] += float(probability or 0.0) / float(len(distributions))
    return _normalize_distribution(dict(merged))


def _consensus_distribution(distributions: Sequence[dict[str, float]], *, min_presence: int) -> dict[str, float]:
    presence_counts = Counter(tag for distribution in distributions for tag in distribution)
    averaged = _average_distributions(distributions)
    return _normalize_distribution(
        {
            tag: probability
            for tag, probability in averaged.items()
            if presence_counts[tag] >= min_presence
        }
    )


def _message_gap_seconds(left: SessionMessage, right: SessionMessage) -> float:
    return abs((right.timestamp - left.timestamp).total_seconds())


def _truncate_messages_around_anchor(
    messages: Sequence[SessionMessage],
    *,
    anchor_index: int,
    max_gap_seconds: float,
) -> list[SessionMessage]:
    if not messages or max_gap_seconds <= 0:
        return list(messages)

    anchor_index = max(0, min(anchor_index, len(messages) - 1))
    start_index = anchor_index
    while start_index > 0:
        if _message_gap_seconds(messages[start_index - 1], messages[start_index]) > max_gap_seconds:
            break
        start_index -= 1

    end_index = anchor_index
    while end_index + 1 < len(messages):
        if _message_gap_seconds(messages[end_index], messages[end_index + 1]) > max_gap_seconds:
            break
        end_index += 1

    return list(messages[start_index : end_index + 1])


def _message_time_range(messages: Sequence[SessionMessage]) -> dict[str, str]:
    if not messages:
        return {"start": "", "end": ""}
    return {
        "start": messages[0].timestamp.isoformat(timespec="seconds"),
        "end": messages[-1].timestamp.isoformat(timespec="seconds"),
    }


def _load_scoped_scene_data(
    session: Session,
    *,
    session_ids: set[str],
    include_global: bool,
) -> ScopedSceneData:
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
        tag: 1.0 + log((float(cluster_count) + 1.0) / (float(count) + 1.0))
        for tag, count in df_counter.items()
        if count > 0
    }
    return ScopedSceneData(
        clusters=clusters,
        tag_lookup=tag_lookup,
        df_by_tag=dict(df_counter),
        idf_by_tag=idf_by_tag,
    )


def _overlap_distribution(left: dict[str, float], right: dict[str, float]) -> float:
    return sum(min(left[tag], right[tag]) for tag in set(left) & set(right))


def _score_distribution_against_scene_clusters(
    session: Session,
    *,
    distribution: dict[str, float],
    scoped_data: ScopedSceneData,
) -> dict[str, Any]:
    cluster_scores: list[dict[str, Any]] = []
    for cluster in scoped_data.clusters:
        if cluster.id is None:
            continue
        cluster_distribution = _distribution_to_mapping(
            _load_cluster_distribution(cluster.tag_distribution),
            tag_lookup=scoped_data.tag_lookup,
        )
        overlap = _overlap_distribution(distribution, cluster_distribution)
        if overlap <= 0:
            continue
        cluster_scores.append(
            {
                "cluster_id": cluster.id,
                "session_id": cluster.session_id,
                "overlap": round(overlap, 4),
                "source_count": cluster.source_count,
                "tag_distribution": _format_distribution(
                    session,
                    cluster_distribution,
                    scoped_data=scoped_data,
                    max_items=8,
                ),
            }
        )
    cluster_scores.sort(key=lambda item: item["overlap"], reverse=True)
    direct_hits = [item for item in cluster_scores if item["overlap"] >= DIRECT_DOMAIN_OVERLAP_THRESHOLD]
    reusable_hits = [item for item in cluster_scores if item["overlap"] >= SCENE_CLUSTER_REUSE_THRESHOLD]
    behavior_scores = _score_behavior_candidates_for_clusters(
        session,
        cluster_scores={
            int(item["cluster_id"]): float(item["overlap"]) * 2.0
            for item in direct_hits[:DIRECT_DOMAIN_OVERLAP_TOPK]
        },
    )
    return {
        "max_overlap": round(cluster_scores[0]["overlap"], 4) if cluster_scores else 0.0,
        "direct_hit_count": len(direct_hits),
        "reuse_hit_count": len(reusable_hits),
        "top_clusters": cluster_scores[:5],
        "candidate_count": len(behavior_scores),
        "top_candidate_score": round(max(behavior_scores.values()), 4) if behavior_scores else 0.0,
    }


def _score_behavior_candidates_for_clusters(
    session: Session,
    *,
    cluster_scores: dict[int, float],
) -> dict[int, float]:
    if not cluster_scores:
        return {}
    paths = session.exec(
        select(BehaviorExperiencePath).where(BehaviorExperiencePath.scene_cluster_id.in_(set(cluster_scores)))  # type: ignore[attr-defined]
    ).all()
    behavior_scores: dict[int, float] = {}
    for path in paths:
        if path.id is None or not path.enabled:
            continue
        cluster_score = cluster_scores.get(path.scene_cluster_id, 0.0)
        if cluster_score <= 0:
            continue
        history_bonus = 1.0 + min(float(path.count or 0), 20.0) * 0.02
        behavior_scores[path.id] = cluster_score * history_bonus
    return dict(sorted(behavior_scores.items(), key=lambda item: item[1], reverse=True))


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


def _format_distribution(
    session: Session,
    distribution: dict[str, float],
    *,
    scoped_data: ScopedSceneData,
    max_items: int,
) -> list[dict[str, Any]]:
    cluster_keys = {tag.split(":", 1)[1] for tag in distribution if ":" in tag}
    labels_by_key = _tag_cluster_labels(session, cluster_keys)
    cluster_count = max(len(scoped_data.clusters), 1)
    items: list[dict[str, Any]] = []
    for tag, probability in sorted(distribution.items(), key=lambda item: item[1], reverse=True)[:max_items]:
        tag_kind, cluster_key = tag.split(":", 1) if ":" in tag else ("", tag)
        labels = labels_by_key.get((tag_kind, cluster_key), [])
        items.append(
            {
                "tag": tag,
                "label": "/".join(labels[:3]) if labels else cluster_key,
                "probability": round(float(probability), 4),
                "df": int(scoped_data.df_by_tag.get(tag, 0)),
                "df_ratio": round(float(scoped_data.df_by_tag.get(tag, 0)) / float(cluster_count), 4),
                "idf": round(float(scoped_data.idf_by_tag.get(tag, 1.0)), 4),
            }
        )
    return items


def _distribution_metrics(distribution: dict[str, float], *, scoped_data: ScopedSceneData) -> dict[str, Any]:
    if not distribution:
        return {
            "tag_count": 0,
            "max_probability": 0.0,
            "weighted_df_ratio": 0.0,
            "weighted_idf": 0.0,
        }
    cluster_count = max(len(scoped_data.clusters), 1)
    weighted_df_ratio = sum(
        probability * float(scoped_data.df_by_tag.get(tag, 0)) / float(cluster_count)
        for tag, probability in distribution.items()
    )
    weighted_idf = sum(
        probability * float(scoped_data.idf_by_tag.get(tag, 1.0))
        for tag, probability in distribution.items()
    )
    return {
        "tag_count": len(distribution),
        "max_probability": round(max(distribution.values()), 4),
        "weighted_df_ratio": round(weighted_df_ratio, 4),
        "weighted_idf": round(weighted_idf, 4),
    }


async def _build_profile_runs(
    window: SourceMessageWindow,
    *,
    client: LLMServiceClient,
    scoped_data: ScopedSceneData,
    temperature: float,
    max_message_gap_minutes: float,
    min_window_messages: int,
) -> list[ProfileRun]:
    variants = [
        ("previous15_current_first15", window.previous_shift_messages),
        ("current30", window.current_messages),
        ("current_last15_next15", window.next_shift_messages),
    ]
    max_gap_seconds = max_message_gap_minutes * 60.0
    runs: list[ProfileRun] = []
    for variant, messages in variants:
        analyzed_messages = _truncate_messages_around_anchor(
            messages,
            anchor_index=len(messages) // 2,
            max_gap_seconds=max_gap_seconds,
        )
        if len(analyzed_messages) < min_window_messages:
            continue
        profile, raw_response = await _analyze_profile_with_llm(analyzed_messages, client=client, temperature=temperature)
        if profile is None:
            continue
        runs.append(
            ProfileRun(
                variant=variant,
                profile=profile,
                raw_response=raw_response,
                distribution=_profile_distribution(profile, tag_lookup=scoped_data.tag_lookup),
                message_count=len(analyzed_messages),
                time_range=_message_time_range(analyzed_messages),
                truncated=len(analyzed_messages) != len(messages),
            )
        )
    return runs


async def build_report(args: Namespace) -> dict[str, Any]:
    base_windows = _select_base_windows(args)
    if len(base_windows) < args.samples:
        raise ValueError(f"可用中心窗口不足: 需要 {args.samples}，实际 {len(base_windows)}")

    samples: list[dict[str, Any]] = []
    client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    continuity_client = (
        LLMServiceClient(task_name="learner", request_type="behavior.scene_continuity_judge")
        if args.continuity_judgement
        else None
    )
    with get_db_session(auto_commit=False) as session:
        for index, window in enumerate(base_windows, start=1):
            session_ids = set() if args.all_sessions else {window.session_id}
            include_global = args.all_sessions or args.include_global
            scoped_data = _load_scoped_scene_data(session, session_ids=session_ids, include_global=include_global)
            runs = await _build_profile_runs(
                window,
                client=client,
                scoped_data=scoped_data,
                temperature=args.temperature,
                max_message_gap_minutes=args.max_message_gap_minutes,
                min_window_messages=args.min_window_messages,
            )
            if len(runs) < 2:
                continue
            continuity = None
            usable_runs = runs
            if continuity_client is not None:
                continuity = await _judge_window_continuity_with_llm(
                    runs,
                    client=continuity_client,
                    temperature=args.continuity_temperature,
                )
                usable_runs = [run for run in runs if run.variant in set(continuity.usable_variants)]
                if not usable_runs:
                    usable_runs = [next((run for run in runs if run.variant == "current30"), runs[0])]
            distributions = [run.distribution for run in runs if run.distribution]
            center_distribution = next((run.distribution for run in runs if run.variant == "current30"), distributions[0])
            average_distribution = _average_distributions(distributions)
            consensus_distribution = _consensus_distribution(distributions, min_presence=min(2, len(distributions)))
            distribution_map = {
                "center": center_distribution,
                "average": average_distribution,
                "consensus": consensus_distribution,
            }
            if continuity is not None:
                usable_distributions = [run.distribution for run in usable_runs if run.distribution]
                distribution_map["llm_filtered_average"] = _average_distributions(usable_distributions)
                distribution_map["llm_filtered_consensus"] = _consensus_distribution(
                    usable_distributions,
                    min_presence=min(2, len(usable_distributions)),
                )
            samples.append(
                {
                    "index": index,
                    "session_id": window.session_id,
                    "chat_name": window.display_name,
                    "continuity": {
                        "is_continuous": continuity.is_continuous,
                        "continuity_score": round(continuity.continuity_score, 4),
                        "usable_variants": continuity.usable_variants,
                        "previous_to_center": continuity.previous_to_center,
                        "center_to_next": continuity.center_to_next,
                        "reason": continuity.reason,
                    }
                    if continuity is not None
                    else None,
                    "time_ranges": {
                        "previous": _message_time_range(window.previous_shift_messages),
                        "center": _message_time_range(window.current_messages),
                        "next": _message_time_range(window.next_shift_messages),
                    },
                    "runs": [
                        {
                            "variant": run.variant,
                            "summary": run.profile.summary,
                            "tag_key": run.profile.tag_cluster_text(),
                            "message_count": run.message_count,
                            "time_range": run.time_range,
                            "truncated": run.truncated,
                            "distribution": _format_distribution(
                                session,
                                run.distribution,
                                scoped_data=scoped_data,
                                max_items=10,
                            ),
                        }
                        for run in runs
                    ],
                    "methods": {
                        method: {
                            "distribution": _format_distribution(
                                session,
                                distribution,
                                scoped_data=scoped_data,
                                max_items=12,
                            ),
                            "metrics": _distribution_metrics(distribution, scoped_data=scoped_data),
                            "matching": _score_distribution_against_scene_clusters(
                                session,
                                distribution=distribution,
                                scoped_data=scoped_data,
                            ),
                        }
                        for method, distribution in distribution_map.items()
                    },
                }
            )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_db": str(_resolve_path(args.source_db)),
        "sample_count": len(samples),
        "requested_samples": args.samples,
        "window_size": args.window_size,
        "half_window": args.half_window,
        "max_message_gap_minutes": args.max_message_gap_minutes,
        "min_window_messages": args.min_window_messages,
        "continuity_judgement": bool(args.continuity_judgement),
        "temperature": args.temperature,
        "continuity_temperature": args.continuity_temperature,
        "scope": "all_sessions" if args.all_sessions else "sample_session_only",
        "include_global": bool(args.all_sessions or args.include_global),
        "summary": _summarize_samples(samples),
        "samples": samples,
    }


def _summarize_samples(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    methods = _report_methods(samples)
    summary: dict[str, Any] = {}
    for method in methods:
        method_items = [sample["methods"][method] for sample in samples if method in sample["methods"]]
        summary[method] = {
            "avg_tag_count": _avg([item["metrics"]["tag_count"] for item in method_items]),
            "avg_weighted_df_ratio": _avg([item["metrics"]["weighted_df_ratio"] for item in method_items]),
            "avg_weighted_idf": _avg([item["metrics"]["weighted_idf"] for item in method_items]),
            "avg_max_overlap": _avg([item["matching"]["max_overlap"] for item in method_items]),
            "avg_direct_hit_count": _avg([item["matching"]["direct_hit_count"] for item in method_items]),
            "avg_reuse_hit_count": _avg([item["matching"]["reuse_hit_count"] for item in method_items]),
            "avg_candidate_count": _avg([item["matching"]["candidate_count"] for item in method_items]),
            "avg_top_candidate_score": _avg([item["matching"]["top_candidate_score"] for item in method_items]),
            "reuse_success_count": sum(1 for item in method_items if item["matching"]["reuse_hit_count"] > 0),
        }
    continuity_items = [sample["continuity"] for sample in samples if sample.get("continuity")]
    if continuity_items:
        summary["continuity"] = {
            "continuous_count": sum(1 for item in continuity_items if item["is_continuous"]),
            "avg_continuity_score": _avg([item["continuity_score"] for item in continuity_items]),
        }
    return summary


def _report_methods(samples: Sequence[dict[str, Any]]) -> list[str]:
    preferred_order = ["center", "average", "consensus", "llm_filtered_average", "llm_filtered_consensus"]
    available_methods: set[str] = set()
    for sample in samples:
        available_methods.update(sample.get("methods", {}))
    return [method for method in preferred_order if method in available_methods]


def _method_label(method: str) -> str:
    labels = {
        "center": "中心30",
        "average": "三窗平均",
        "consensus": "三窗共识",
        "llm_filtered_average": "LLM筛选平均",
        "llm_filtered_consensus": "LLM筛选共识",
    }
    return labels.get(method, method)


def _avg(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    return round(sum(float(value) for value in values) / float(len(values)), 4)


def _distribution_line(item: dict[str, Any]) -> str:
    return (
        f"- {item.get('label')} p={item.get('probability')} "
        f"df={item.get('df')} df_ratio={item.get('df_ratio')} idf={item.get('idf')}"
    )


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# 行为场景增强 AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 源数据库：`{report['source_db']}`")
    lines.append(f"- 样本数：{report['sample_count']} / 请求 {report['requested_samples']}")
    lines.append(f"- 中心窗口：{report['window_size']} 条")
    lines.append(f"- 前后滑动：{report['half_window']} 条")
    lines.append(f"- 最大相邻消息间隔：{report.get('max_message_gap_minutes')} 分钟")
    lines.append(f"- 最小分析窗口消息数：{report.get('min_window_messages')}")
    lines.append(f"- LLM顺承判断：{report.get('continuity_judgement')}")
    lines.append(f"- temperature：{report['temperature']}")
    if report.get("continuity_judgement"):
        lines.append(f"- continuity_temperature：{report.get('continuity_temperature')}")
    lines.append(f"- 范围：{report['scope']}，include_global={report['include_global']}")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 方法 | tag数 | weighted_df | weighted_idf | max_overlap | direct命中 | reuse命中 | 候选数 | top候选分 | 有reuse样本 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for method in _report_methods(report["samples"]):
        label = _method_label(method)
        item = report["summary"][method]
        lines.append(
            f"| {label} | {item['avg_tag_count']} | {item['avg_weighted_df_ratio']} | "
            f"{item['avg_weighted_idf']} | {item['avg_max_overlap']} | {item['avg_direct_hit_count']} | "
            f"{item['avg_reuse_hit_count']} | {item['avg_candidate_count']} | "
            f"{item['avg_top_candidate_score']} | {item['reuse_success_count']} |"
        )
    if "continuity" in report["summary"]:
        continuity_summary = report["summary"]["continuity"]
        lines.append("")
        lines.append(
            f"- 顺承样本：{continuity_summary['continuous_count']} / {report['sample_count']}，"
            f"平均顺承分：{continuity_summary['avg_continuity_score']}"
        )
    for sample in report["samples"]:
        lines.append("")
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- center：{sample['time_ranges']['center']['start']} ~ {sample['time_ranges']['center']['end']}")
        if sample.get("continuity"):
            continuity = sample["continuity"]
            lines.append(
                f"- 顺承判断：continuous={continuity['is_continuous']} "
                f"score={continuity['continuity_score']} usable={continuity['usable_variants']}"
            )
            lines.append(f"- 顺承原因：{continuity['reason']}")
        for run in sample["runs"]:
            lines.append(
                f"- 窗口 {run['variant']}：messages={run['message_count']} "
                f"truncated={run['truncated']} range={run['time_range']['start']} ~ {run['time_range']['end']}"
            )
        for method in _report_methods([sample]):
            label = _method_label(method)
            method_item = sample["methods"][method]
            matching = method_item["matching"]
            lines.append("")
            lines.append(f"### {label}")
            lines.append(f"- metrics：`{json.dumps(method_item['metrics'], ensure_ascii=False)}`")
            lines.append(
                f"- matching：max_overlap={matching['max_overlap']} "
                f"direct_hit={matching['direct_hit_count']} reuse_hit={matching['reuse_hit_count']} "
                f"candidate_count={matching['candidate_count']} top_candidate_score={matching['top_candidate_score']}"
            )
            for item in method_item["distribution"][:8]:
                lines.append(_distribution_line(item))
            if matching["top_clusters"]:
                top_cluster = matching["top_clusters"][0]
                lines.append(f"- top_cluster：#{top_cluster['cluster_id']} overlap={top_cluster['overlap']}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="比较中心场景单画像与滑动窗口增强画像的合并/检索潜力。")
    parser.add_argument("--source-db", default="data/MaiBot.db")
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--half-window", type=int, default=DEFAULT_HALF_WINDOW)
    parser.add_argument("--max-message-gap-minutes", type=float, default=DEFAULT_MAX_MESSAGE_GAP_MINUTES)
    parser.add_argument("--min-window-messages", type=int, default=DEFAULT_MIN_WINDOW_MESSAGES)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--continuity-judgement", action="store_true")
    parser.add_argument("--continuity-temperature", type=float, default=0.0)
    parser.add_argument("--include-global", action="store_true")
    parser.add_argument("--all-sessions", action="store_true")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        args = parse_args()
        report = asyncio.run(build_report(args))
        output_path = PROJECT_ROOT / args.output
        json_output_path = PROJECT_ROOT / args.json_output
        write_markdown_report(report, output_path)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Markdown report: {output_path}")
        print(f"JSON report: {json_output_path}")
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

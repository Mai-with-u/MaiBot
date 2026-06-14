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
    DIRECT_DOMAIN_OVERLAP_THRESHOLD,
    DIRECT_DOMAIN_OVERLAP_TOPK,
    _distribution_to_mapping,
    _load_cluster_distribution,
    _load_tag_cluster_lookup,
    _scene_cluster_matches_sessions,
    _score_behavior_clusters,
    build_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_scene_distribution_repeats_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_scene_distribution_repeats_abtest.json"


@dataclass(frozen=True)
class MessageSample:
    message_id: str
    timestamp: datetime
    session_id: str
    chat_name: str
    speaker: str
    text: str


@dataclass(frozen=True)
class ProfileRun:
    profile: BehaviorScenarioProfile
    raw_response: str
    distribution: dict[str, float]


@dataclass(frozen=True)
class ScopedClusters:
    clusters: list[BehaviorSceneCluster]
    tag_lookup: dict[tuple[str, str], str]
    df_by_tag: dict[str, int]
    idf_by_tag: dict[str, float]


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
    temperature: float,
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


def _normalize_distribution(tag_weights: dict[str, float]) -> dict[str, float]:
    total_weight = sum(max(float(weight or 0.0), 0.0) for weight in tag_weights.values())
    if total_weight <= 0:
        return {}
    return {
        tag: max(float(weight or 0.0), 0.0) / total_weight
        for tag, weight in sorted(tag_weights.items())
        if weight > 0
    }


def _profile_distribution(
    profile: BehaviorScenarioProfile,
    *,
    tag_lookup: dict[tuple[str, str], str],
) -> dict[str, float]:
    distribution = build_scene_cluster_distribution(profile, tag_lookup=tag_lookup)
    return _distribution_to_mapping(distribution, tag_lookup=tag_lookup)


def _average_distributions(distributions: Sequence[dict[str, float]]) -> dict[str, float]:
    if not distributions:
        return {}
    tag_weights: dict[str, float] = defaultdict(float)
    for distribution in distributions:
        for tag, probability in distribution.items():
            tag_weights[tag] += float(probability or 0.0) / float(len(distributions))
    return _normalize_distribution(dict(tag_weights))


def _filter_distribution_by_presence(
    distribution: dict[str, float],
    *,
    presence_counts: Counter[str],
    min_presence: int,
) -> dict[str, float]:
    return _normalize_distribution(
        {
            tag: probability
            for tag, probability in distribution.items()
            if int(presence_counts.get(tag, 0)) >= min_presence
        }
    )


def _distribution_overlap(left: dict[str, float], right: dict[str, float]) -> float:
    shared_tags = set(left) & set(right)
    return round(sum(min(float(left[tag]), float(right[tag])) for tag in shared_tags), 4)


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
        df_by_tag=dict(df_counter),
        idf_by_tag=idf_by_tag,
    )


def _score_scene_clusters_by_direct_distribution(
    *,
    direct_distribution: dict[str, float],
    scoped_clusters: ScopedClusters,
    topk: int,
) -> dict[int, float]:
    if not direct_distribution:
        return {}

    cluster_scores: dict[int, float] = {}
    for cluster in scoped_clusters.clusters:
        if cluster.id is None:
            continue
        cluster_tags = _distribution_to_mapping(
            _load_cluster_distribution(cluster.tag_distribution),
            tag_lookup=scoped_clusters.tag_lookup,
        )
        if not cluster_tags:
            continue
        shared_tags = set(direct_distribution) & set(cluster_tags)
        if not shared_tags:
            continue
        direct_overlap = sum(min(direct_distribution[tag], cluster_tags[tag]) for tag in shared_tags)
        if direct_overlap < DIRECT_DOMAIN_OVERLAP_THRESHOLD:
            continue
        cluster_scores[int(cluster.id)] = round(direct_overlap * 2.0, 4)
    return dict(sorted(cluster_scores.items(), key=lambda item: item[1], reverse=True)[:topk])


def _retrieve_direct_candidates(
    session: Session,
    *,
    direct_distribution: dict[str, float],
    scoped_clusters: ScopedClusters,
    session_ids: set[str],
    include_global: bool,
    query_session_id: str,
    max_count: int,
) -> list[dict[str, Any]]:
    cluster_scores = _score_scene_clusters_by_direct_distribution(
        direct_distribution=direct_distribution,
        scoped_clusters=scoped_clusters,
        topk=DIRECT_DOMAIN_OVERLAP_TOPK,
    )
    behavior_scores = _score_behavior_clusters(
        session,
        cluster_scores=cluster_scores,
        session_ids=session_ids,
        include_global=include_global,
    )
    sorted_scores = dict(sorted(behavior_scores.items(), key=lambda item: item[1], reverse=True)[:max_count])
    return _candidate_payloads(
        session,
        behavior_scores=sorted_scores,
        query_session_id=query_session_id,
    )


def _candidate_payloads(
    session: Session,
    *,
    behavior_scores: dict[int, float],
    query_session_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for behavior_id, score in behavior_scores.items():
        path = session.get(BehaviorExperiencePath, behavior_id)
        if path is None:
            continue
        action = session.get(BehaviorAction, path.action_id)
        outcome = session.get(BehaviorOutcome, path.outcome_id)
        candidates.append(
            {
                "id": path.id,
                "retrieval_score": round(float(score or 0.0), 4),
                "same_chat": path.session_id == query_session_id,
                "session_id": path.session_id,
                "scene_cluster_id": path.scene_cluster_id,
                "action": action.action if action is not None else "",
                "outcome": outcome.outcome if outcome is not None else "",
                "count": path.count,
                "success_count": path.success_count,
                "failure_count": path.failure_count,
                "path_score": path.score,
            }
        )
    return candidates


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
    scoped_clusters: ScopedClusters,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    cluster_keys = {tag.split(":", 1)[1] for tag in distribution if ":" in tag}
    labels_by_key = _tag_cluster_labels(session, cluster_keys)
    cluster_count = max(len(scoped_clusters.clusters), 1)
    items: list[dict[str, Any]] = []
    for tag, probability in sorted(distribution.items(), key=lambda item: item[1], reverse=True)[:max_items]:
        tag_kind, cluster_key = tag.split(":", 1) if ":" in tag else ("", tag)
        labels = labels_by_key.get((tag_kind, cluster_key), [])
        items.append(
            {
                "tag": tag,
                "label": "/".join(labels[:3]) if labels else cluster_key,
                "probability": round(float(probability or 0.0), 4),
                "df": int(scoped_clusters.df_by_tag.get(tag, 0)),
                "df_ratio": round(float(scoped_clusters.df_by_tag.get(tag, 0)) / float(cluster_count), 4),
                "idf": round(float(scoped_clusters.idf_by_tag.get(tag, 1.0)), 4),
            }
        )
    return items


def _distribution_metrics(
    distribution: dict[str, float],
    *,
    scoped_clusters: ScopedClusters,
    presence_counts: Counter[str],
    repeats: int,
) -> dict[str, Any]:
    if not distribution:
        return {
            "tag_count": 0,
            "max_probability": 0.0,
            "weighted_df_ratio": 0.0,
            "weighted_idf": 0.0,
            "consensus_mass": 0.0,
            "single_run_only_mass": 0.0,
        }

    cluster_count = max(len(scoped_clusters.clusters), 1)
    weighted_df_ratio = sum(
        float(probability) * float(scoped_clusters.df_by_tag.get(tag, 0)) / float(cluster_count)
        for tag, probability in distribution.items()
    )
    weighted_idf = sum(
        float(probability) * float(scoped_clusters.idf_by_tag.get(tag, 1.0))
        for tag, probability in distribution.items()
    )
    consensus_threshold = min(repeats, 2)
    consensus_mass = sum(
        float(probability)
        for tag, probability in distribution.items()
        if int(presence_counts.get(tag, 0)) >= consensus_threshold
    )
    single_run_only_mass = sum(
        float(probability)
        for tag, probability in distribution.items()
        if int(presence_counts.get(tag, 0)) <= 1
    )
    return {
        "tag_count": len(distribution),
        "max_probability": round(max(distribution.values()), 4),
        "weighted_df_ratio": round(weighted_df_ratio, 4),
        "weighted_idf": round(weighted_idf, 4),
        "consensus_mass": round(consensus_mass, 4),
        "single_run_only_mass": round(single_run_only_mass, 4),
    }


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


def _compare_candidates(
    current_candidates: list[dict[str, Any]],
    averaged_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    current_ids = [candidate["id"] for candidate in current_candidates if candidate.get("id") is not None]
    averaged_ids = [candidate["id"] for candidate in averaged_candidates if candidate.get("id") is not None]
    current_set = set(current_ids)
    averaged_set = set(averaged_ids)
    union_count = len(current_set | averaged_set)
    overlap_count = len(current_set & averaged_set)
    return {
        "top1_changed": bool(current_ids and averaged_ids and current_ids[0] != averaged_ids[0]),
        "current_top1": current_ids[0] if current_ids else None,
        "averaged_top1": averaged_ids[0] if averaged_ids else None,
        "overlap_count": overlap_count,
        "jaccard": round(float(overlap_count) / float(union_count), 4) if union_count else 1.0,
    }


async def _run_profile_repeats(
    window: Sequence[MessageSample],
    *,
    client: LLMServiceClient,
    scoped_clusters: ScopedClusters,
    repeats: int,
    temperature: float,
) -> list[ProfileRun]:
    runs: list[ProfileRun] = []
    for _ in range(repeats):
        profile, raw_response = await _analyze_profile_with_llm(window, client=client, temperature=temperature)
        if profile is None:
            continue
        runs.append(
            ProfileRun(
                profile=profile,
                raw_response=raw_response,
                distribution=_profile_distribution(profile, tag_lookup=scoped_clusters.tag_lookup),
            )
        )
    return runs


async def _build_sample_result(
    *,
    index: int,
    window: Sequence[MessageSample],
    client: LLMServiceClient,
    args: Namespace,
) -> dict[str, Any] | None:
    session_ids = set() if args.all_sessions else {window[0].session_id}
    include_global = args.all_sessions or args.include_global

    with get_db_session(auto_commit=False) as session:
        scoped_clusters = _load_scoped_clusters(session, session_ids=session_ids, include_global=include_global)
        profile_runs = await _run_profile_repeats(
            window,
            client=client,
            scoped_clusters=scoped_clusters,
            repeats=args.repeats,
            temperature=args.temperature,
        )
        if len(profile_runs) < 2:
            return None

        distributions = [run.distribution for run in profile_runs if run.distribution]
        if not distributions:
            return None

        current_distribution = distributions[0]
        averaged_distribution = _average_distributions(distributions)
        presence_counts = Counter(tag for distribution in distributions for tag in distribution)
        consensus_distribution = _filter_distribution_by_presence(
            averaged_distribution,
            presence_counts=presence_counts,
            min_presence=min(len(distributions), 2),
        )
        current_candidates = _retrieve_direct_candidates(
            session,
            direct_distribution=current_distribution,
            scoped_clusters=scoped_clusters,
            session_ids=session_ids,
            include_global=include_global,
            query_session_id=window[0].session_id,
            max_count=args.max_count,
        )
        averaged_candidates = _retrieve_direct_candidates(
            session,
            direct_distribution=averaged_distribution,
            scoped_clusters=scoped_clusters,
            session_ids=session_ids,
            include_global=include_global,
            query_session_id=window[0].session_id,
            max_count=args.max_count,
        )
        consensus_candidates = _retrieve_direct_candidates(
            session,
            direct_distribution=consensus_distribution,
            scoped_clusters=scoped_clusters,
            session_ids=session_ids,
            include_global=include_global,
            query_session_id=window[0].session_id,
            max_count=args.max_count,
        )
        current_to_other_overlap = 0.0
        if len(distributions) > 1:
            current_to_other_overlap = sum(
                _distribution_overlap(current_distribution, distribution)
                for distribution in distributions[1:]
            ) / float(len(distributions) - 1)
        averaged_to_runs_overlap = sum(
            _distribution_overlap(averaged_distribution, distribution)
            for distribution in distributions
        ) / float(len(distributions))

        return {
            "index": index,
            "session_id": window[0].session_id,
            "chat_name": window[0].chat_name,
            "time_range": {
                "start": window[0].timestamp.isoformat(timespec="seconds"),
                "end": window[-1].timestamp.isoformat(timespec="seconds"),
            },
            "context": _message_payload(window),
            "runs": [
                {
                    "profile": _profile_payload(run.profile),
                    "distribution": _format_distribution(
                        session,
                        run.distribution,
                        scoped_clusters=scoped_clusters,
                    ),
                    "raw_response": run.raw_response,
                }
                for run in profile_runs
            ],
            "current": {
                "distribution": _format_distribution(
                    session,
                    current_distribution,
                    scoped_clusters=scoped_clusters,
                ),
                "metrics": _distribution_metrics(
                    current_distribution,
                    scoped_clusters=scoped_clusters,
                    presence_counts=presence_counts,
                    repeats=len(distributions),
                ),
                "direct_candidates": current_candidates,
            },
            "averaged": {
                "distribution": _format_distribution(
                    session,
                    averaged_distribution,
                    scoped_clusters=scoped_clusters,
                ),
                "metrics": _distribution_metrics(
                    averaged_distribution,
                    scoped_clusters=scoped_clusters,
                    presence_counts=presence_counts,
                    repeats=len(distributions),
                ),
                "direct_candidates": averaged_candidates,
            },
            "consensus": {
                "distribution": _format_distribution(
                    session,
                    consensus_distribution,
                    scoped_clusters=scoped_clusters,
                ),
                "metrics": _distribution_metrics(
                    consensus_distribution,
                    scoped_clusters=scoped_clusters,
                    presence_counts=presence_counts,
                    repeats=len(distributions),
                ),
                "direct_candidates": consensus_candidates,
            },
            "compare": {
                "current_to_other_overlap": round(current_to_other_overlap, 4),
                "averaged_to_runs_overlap": round(averaged_to_runs_overlap, 4),
                "retrieval": _compare_candidates(current_candidates, averaged_candidates),
                "consensus_retrieval": _compare_candidates(current_candidates, consensus_candidates),
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
        sample = await _build_sample_result(index=index, window=window, client=llm_client, args=args)
        if sample is not None:
            samples.append(sample)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": args.days,
        "requested_samples": args.samples,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "repeats": args.repeats,
        "temperature": args.temperature,
        "seed": args.seed,
        "scope": "all_sessions" if args.all_sessions else "sample_session_only",
        "include_global": bool(args.all_sessions or args.include_global),
        "summary": _summarize_samples(samples),
        "samples": samples,
    }


def _summarize_samples(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "avg_current_consensus_mass": 0.0,
            "avg_averaged_consensus_mass": 0.0,
            "avg_consensus_consensus_mass": 0.0,
            "avg_current_single_run_only_mass": 0.0,
            "avg_averaged_single_run_only_mass": 0.0,
            "avg_consensus_single_run_only_mass": 0.0,
            "avg_current_weighted_df_ratio": 0.0,
            "avg_averaged_weighted_df_ratio": 0.0,
            "avg_consensus_weighted_df_ratio": 0.0,
            "avg_current_to_other_overlap": 0.0,
            "avg_averaged_to_runs_overlap": 0.0,
            "retrieval_top1_changed_count": 0,
            "consensus_retrieval_top1_changed_count": 0,
        }

    return {
        "avg_current_consensus_mass": _avg_metric(samples, "current", "consensus_mass"),
        "avg_averaged_consensus_mass": _avg_metric(samples, "averaged", "consensus_mass"),
        "avg_consensus_consensus_mass": _avg_metric(samples, "consensus", "consensus_mass"),
        "avg_current_single_run_only_mass": _avg_metric(samples, "current", "single_run_only_mass"),
        "avg_averaged_single_run_only_mass": _avg_metric(samples, "averaged", "single_run_only_mass"),
        "avg_consensus_single_run_only_mass": _avg_metric(samples, "consensus", "single_run_only_mass"),
        "avg_current_weighted_df_ratio": _avg_metric(samples, "current", "weighted_df_ratio"),
        "avg_averaged_weighted_df_ratio": _avg_metric(samples, "averaged", "weighted_df_ratio"),
        "avg_consensus_weighted_df_ratio": _avg_metric(samples, "consensus", "weighted_df_ratio"),
        "avg_current_weighted_idf": _avg_metric(samples, "current", "weighted_idf"),
        "avg_averaged_weighted_idf": _avg_metric(samples, "averaged", "weighted_idf"),
        "avg_consensus_weighted_idf": _avg_metric(samples, "consensus", "weighted_idf"),
        "avg_current_tag_count": _avg_metric(samples, "current", "tag_count"),
        "avg_averaged_tag_count": _avg_metric(samples, "averaged", "tag_count"),
        "avg_consensus_tag_count": _avg_metric(samples, "consensus", "tag_count"),
        "avg_current_to_other_overlap": round(
            sum(float(sample["compare"]["current_to_other_overlap"]) for sample in samples) / float(len(samples)),
            4,
        ),
        "avg_averaged_to_runs_overlap": round(
            sum(float(sample["compare"]["averaged_to_runs_overlap"]) for sample in samples) / float(len(samples)),
            4,
        ),
        "retrieval_top1_changed_count": sum(
            1 for sample in samples if sample["compare"]["retrieval"].get("top1_changed")
        ),
        "consensus_retrieval_top1_changed_count": sum(
            1 for sample in samples if sample["compare"]["consensus_retrieval"].get("top1_changed")
        ),
        "retrieval_avg_jaccard": round(
            sum(float(sample["compare"]["retrieval"].get("jaccard") or 0.0) for sample in samples)
            / float(len(samples)),
            4,
        ),
        "consensus_retrieval_avg_jaccard": round(
            sum(float(sample["compare"]["consensus_retrieval"].get("jaccard") or 0.0) for sample in samples)
            / float(len(samples)),
            4,
        ),
    }


def _avg_metric(samples: Sequence[dict[str, Any]], section: str, metric: str) -> float:
    return round(
        sum(float(sample[section]["metrics"].get(metric) or 0.0) for sample in samples) / float(len(samples)),
        4,
    )


def _candidate_line(candidate: dict[str, Any]) -> str:
    return (
        f"- #{candidate.get('id')} score={candidate.get('retrieval_score')} "
        f"cluster=#{candidate.get('scene_cluster_id')} 行为：{candidate.get('action')}；结果：{candidate.get('outcome')}"
    )


def _distribution_line(item: dict[str, Any]) -> str:
    return (
        f"- {item.get('label')} p={item.get('probability')} "
        f"df={item.get('df')} df_ratio={item.get('df_ratio')} idf={item.get('idf')}"
    )


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# 行为场景画像三次抽样分布 AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 抽样范围：近 {report['days']} 天")
    lines.append(f"- 样本数：{report['sample_count']} / 请求 {report['requested_samples']}")
    lines.append(f"- 每个场景抽样次数：{report['repeats']}")
    lines.append(f"- temperature：{report['temperature']}")
    lines.append(f"- 窗口大小：{report['window_size']}")
    lines.append(f"- seed：`{report['seed']}`")
    lines.append(f"- 范围：{report['scope']}，include_global={report['include_global']}")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 单次分布共识质量：{summary['avg_current_consensus_mass']}")
    lines.append(f"- 三次平均分布共识质量：{summary['avg_averaged_consensus_mass']}")
    lines.append(f"- 三次共识平均分布共识质量：{summary['avg_consensus_consensus_mass']}")
    lines.append(f"- 单次分布一次性 tag 质量：{summary['avg_current_single_run_only_mass']}")
    lines.append(f"- 三次平均分布一次性 tag 质量：{summary['avg_averaged_single_run_only_mass']}")
    lines.append(f"- 三次共识平均分布一次性 tag 质量：{summary['avg_consensus_single_run_only_mass']}")
    lines.append(f"- 单次 weighted df_ratio：{summary['avg_current_weighted_df_ratio']}")
    lines.append(f"- 三次平均 weighted df_ratio：{summary['avg_averaged_weighted_df_ratio']}")
    lines.append(f"- 三次共识平均 weighted df_ratio：{summary['avg_consensus_weighted_df_ratio']}")
    lines.append(f"- 单次 weighted idf：{summary['avg_current_weighted_idf']}")
    lines.append(f"- 三次平均 weighted idf：{summary['avg_averaged_weighted_idf']}")
    lines.append(f"- 三次共识平均 weighted idf：{summary['avg_consensus_weighted_idf']}")
    lines.append(f"- 单次平均 tag 数：{summary['avg_current_tag_count']}")
    lines.append(f"- 三次平均平均 tag 数：{summary['avg_averaged_tag_count']}")
    lines.append(f"- 三次共识平均 tag 数：{summary['avg_consensus_tag_count']}")
    lines.append(f"- 单次对其他 runs 平均 overlap：{summary['avg_current_to_other_overlap']}")
    lines.append(f"- 三次平均对各 run 平均 overlap：{summary['avg_averaged_to_runs_overlap']}")
    lines.append(f"- direct 召回 Top1 变化：{summary['retrieval_top1_changed_count']} / {report['sample_count']}")
    lines.append(f"- direct 召回平均 Jaccard：{summary['retrieval_avg_jaccard']}")
    lines.append(
        f"- direct 召回 Top1 变化（共识平均）："
        f"{summary['consensus_retrieval_top1_changed_count']} / {report['sample_count']}"
    )
    lines.append(f"- direct 召回平均 Jaccard（共识平均）：{summary['consensus_retrieval_avg_jaccard']}")
    lines.append("")

    for sample in report["samples"]:
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间范围：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append(f"- current_to_other_overlap：{sample['compare']['current_to_other_overlap']}")
        lines.append(f"- averaged_to_runs_overlap：{sample['compare']['averaged_to_runs_overlap']}")
        lines.append(f"- direct Top1 变化：{sample['compare']['retrieval']['top1_changed']}")
        lines.append("")
        lines.append("### 上下文")
        for message in sample["context"]:
            lines.append(f"- `{message['time']}` **{message['speaker']}**：{message['text']}")
        lines.append("")
        lines.append("### 单次分布")
        lines.append(f"- metrics：`{json.dumps(sample['current']['metrics'], ensure_ascii=False)}`")
        for item in sample["current"]["distribution"]:
            lines.append(_distribution_line(item))
        lines.append("")
        lines.append("### 三次平均分布")
        lines.append(f"- metrics：`{json.dumps(sample['averaged']['metrics'], ensure_ascii=False)}`")
        for item in sample["averaged"]["distribution"]:
            lines.append(_distribution_line(item))
        lines.append("")
        lines.append("### 三次共识平均分布")
        lines.append(f"- metrics：`{json.dumps(sample['consensus']['metrics'], ensure_ascii=False)}`")
        for item in sample["consensus"]["distribution"]:
            lines.append(_distribution_line(item))
        lines.append("")
        lines.append("### Direct 召回对比")
        lines.append("单次：")
        if not sample["current"]["direct_candidates"]:
            lines.append("- 无命中")
        for candidate in sample["current"]["direct_candidates"][:5]:
            lines.append(_candidate_line(candidate))
        lines.append("")
        lines.append("三次平均：")
        if not sample["averaged"]["direct_candidates"]:
            lines.append("- 无命中")
        for candidate in sample["averaged"]["direct_candidates"][:5]:
            lines.append(_candidate_line(candidate))
        lines.append("")
        lines.append("三次共识平均：")
        if not sample["consensus"]["direct_candidates"]:
            lines.append("- 无命中")
        for candidate in sample["consensus"]["direct_candidates"][:5]:
            lines.append(_candidate_line(candidate))
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="同一聊天窗口多次抽取行为场景画像，比较单次分布与多次平均分布。")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--window-size", type=int, default=8)
    parser.add_argument("--max-gap-minutes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.2)
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

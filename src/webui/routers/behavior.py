"""行为学习图谱浏览 API。"""

from collections import defaultdict
from datetime import datetime
from itertools import combinations
from typing import Annotated, Any, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import col, func, select

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
    BehaviorSceneCluster,
    BehaviorSceneTagCluster,
    ChatSession,
)
from src.learners.behavior_scenario import (
    BehaviorScenarioProfile,
    BehaviorScenarioTagCluster,
    behavior_scenario_analyzer,
    parse_behavior_scenario_response,
)
from src.learners.behavior_scene_cluster_store import (
    _load_cluster_distribution,
    debug_retrieve_behavior_scores_from_scene_clusters,
    format_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.services.llm_service import LLMServiceClient
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/behavior", tags=["Behavior"], dependencies=[Depends(require_auth)])
behavior_scene_debug_model = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")


class BehaviorChatInfo(BaseModel):
    session_id: str
    display_name: str
    platform: str = ""
    account_id: Optional[str] = None
    chat_type: str = ""
    path_count: int = 0
    cluster_count: int = 0
    scene_count: int = 0
    last_active_time: Optional[str] = None


class BehaviorClusterTag(BaseModel):
    tag: str
    probability: float = 0.0
    display: str = ""


class BehaviorSceneClusterPayload(BaseModel):
    id: Optional[int] = None
    name: str = ""
    tags: list[BehaviorClusterTag] = Field(default_factory=list)
    source_count: int = 0
    update_time: Optional[str] = None


class BehaviorPathItem(BaseModel):
    id: int
    session_id: Optional[str] = None
    chat_name: str = ""
    scene_cluster_id: Optional[int] = None
    scene_cluster_name: str = ""
    scene_cluster_tags: list[BehaviorClusterTag] = Field(default_factory=list)
    scene_cluster_source_count: int = 0
    actor_type: str = "other_user"
    learning_type: str = "observed_behavior"
    action: str = ""
    outcome: str = ""
    count: int = 0
    activation_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    score: float = 0.0
    enabled: bool = True
    last_active_time: Optional[str] = None
    last_feedback_time: Optional[str] = None
    update_time: Optional[str] = None


class BehaviorPathListResponse(BaseModel):
    success: bool = True
    total: int
    page: int
    page_size: int
    data: list[BehaviorPathItem]


class BehaviorClusterItem(BehaviorSceneClusterPayload):
    session_id: Optional[str] = None
    chat_name: str = ""
    path_count: int = 0
    enabled_path_count: int = 0
    activation_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    observed_path_count: int = 0
    self_reflection_path_count: int = 0
    last_active_time: Optional[str] = None


class BehaviorClusterListResponse(BaseModel):
    success: bool = True
    total: int
    page: int
    page_size: int
    data: list[BehaviorClusterItem]


class BehaviorGraphDataResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class BehaviorNodePayload(BaseModel):
    id: int
    kind: str
    label: str
    score: float = 0.0
    source_count: int = 0


class BehaviorEdgePayload(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    weight: float = 1.0
    count: int = 0


class BehaviorPathDetailResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class BehaviorScenarioDebugRequest(BaseModel):
    session_id: Optional[str] = Field(default=None)
    include_global: bool = Field(default=True)
    retrieval_mode: str = Field(default="tag_cluster_spread_1")
    scene_text: str = Field(default="")
    summary: str = Field(default="")
    tag_clusters: list[dict[str, Any]] = Field(default_factory=list)
    need: dict[str, Any] = Field(default_factory=dict)
    other_traits: list[dict[str, Any]] = Field(default_factory=list)
    max_count: int = Field(default=20, ge=1, le=80)


def _isoformat(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if value:
        return str(value)
    return None


def _load_json_list(raw_value: Any) -> list[Any]:
    if not raw_value:
        return []
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, str):
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _cluster_tag_payloads(
    raw_value: Any,
    members_by_key: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> list[BehaviorClusterTag]:
    tags: list[BehaviorClusterTag] = []
    for item in _load_json_list(raw_value):
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        if not tag:
            continue
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            probability = 0.0
        display = _tag_ref_display(tag, members_by_key) if members_by_key is not None else ""
        tags.append(BehaviorClusterTag(tag=tag, probability=max(probability, 0.0), display=display))
    return sorted(tags, key=lambda item: item.probability, reverse=True)


def _format_cluster_tag_payloads(tags: list[BehaviorClusterTag]) -> str:
    parts = [
        f"{(tag.display or tag.tag)}={tag.probability:.3f}"
        for tag in tags[:8]
        if (tag.display or tag.tag)
    ]
    return "；".join(parts)


def _cluster_payload(
    cluster: Optional[BehaviorSceneCluster],
    members_by_key: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> BehaviorSceneClusterPayload:
    if cluster is None:
        return BehaviorSceneClusterPayload()
    tags = _cluster_tag_payloads(cluster.tag_distribution, members_by_key)
    return BehaviorSceneClusterPayload(
        id=cluster.id,
        name=_format_cluster_tag_payloads(tags)
        or format_scene_cluster_distribution(_load_cluster_distribution(cluster.tag_distribution)),
        tags=tags,
        source_count=int(cluster.source_count or 0),
        update_time=_isoformat(cluster.update_time),
    )


async def _analyze_debug_scene_text(scene_text: str) -> BehaviorScenarioProfile:
    """使用主程序行为场景概括提示，把一句话调试输入转换为场景 tag 簇。"""

    normalized_scene_text = " ".join(scene_text.split()).strip()
    if not normalized_scene_text:
        return BehaviorScenarioProfile()

    async def run_scene_prompt(system_prompt: str) -> str:
        scene_messages = _build_debug_scene_messages(normalized_scene_text, system_prompt)
        generation_result = await behavior_scene_debug_model.generate_response_with_messages(
            lambda _client: scene_messages,
            options=LLMGenerationOptions(temperature=0.2),
        )
        return generation_result.response or ""

    profile = await behavior_scenario_analyzer.analyze(
        context_text=normalized_scene_text,
        sub_agent_runner=run_scene_prompt,
    )
    if not profile.summary:
        profile = BehaviorScenarioProfile(
            summary=normalized_scene_text,
            tag_clusters=profile.tag_clusters,
            confidence=profile.confidence,
        )
    return profile


def _build_debug_scene_messages(scene_text: str, system_prompt: str) -> list[Message]:
    """把 WebUI 调试输入包装成场景概括模型熟悉的多消息格式。"""

    return [
        MessageBuilder()
        .set_role(RoleType.System)
        .add_text_content(
            f"{system_prompt}\n\n"
            "注意：聊天场景会在后续 user message 中给出。该消息是 WebUI 调试输入的一句话场景描述，"
            "不是数据库中的真实聊天记录；请仍按主程序行为学习场景概括的 JSON 结构输出 tag 簇。"
        )
        .build(),
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content(
            "\n".join(
                [
                    "[source_id:webui-debug-1]",
                    "[speaker:USER]",
                    "[name:WebUI 调试输入]",
                    "[content]",
                    scene_text,
                ]
            )
        )
        .build(),
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content("请根据以上聊天场景描述输出场景片段 JSON。")
        .build(),
    ]


def _profile_debug_payload(profile: BehaviorScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "confidence": profile.confidence,
        "tag_clusters": [_tag_cluster_debug_payload(cluster) for cluster in profile.tag_clusters],
    }


def _tag_cluster_debug_payload(cluster: BehaviorScenarioTagCluster) -> dict[str, Any]:
    return {
        "kind": cluster.kind,
        "tags": cluster.all_values(),
    }


def _split_tag_ref(tag_ref: str) -> tuple[str, str]:
    if ":" not in tag_ref:
        return "", tag_ref
    tag_kind, cluster_key = tag_ref.split(":", 1)
    return tag_kind, cluster_key


def _tag_cluster_members_by_key(session: Any) -> dict[str, list[dict[str, Any]]]:
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = session.exec(
        select(BehaviorSceneTagCluster).order_by(
            BehaviorSceneTagCluster.cluster_key,
            BehaviorSceneTagCluster.source_count.desc(),  # type: ignore[attr-defined]
            BehaviorSceneTagCluster.tag,
        )
    ).all()
    for row in rows:
        members[str(row.cluster_key)].append(
            {
                "kind": row.tag_kind,
                "tag": row.tag,
                "source_count": int(row.source_count or 0),
            }
        )
    return members


def _tag_ref_display(tag_ref: str, members_by_key: Optional[dict[str, list[dict[str, Any]]]]) -> str:
    if members_by_key is None:
        return tag_ref
    tag_kind, cluster_key = _split_tag_ref(tag_ref)
    members = members_by_key.get(cluster_key, [])
    if not members:
        return tag_ref
    names: list[str] = []
    for member in members:
        if tag_kind and member["kind"] != tag_kind:
            continue
        tag = str(member["tag"] or "")
        if tag and tag not in names:
            names.append(tag)
        if len(names) >= 2:
            break
    if not names:
        for member in members[:2]:
            tag = str(member["tag"] or "")
            if tag and tag not in names:
                names.append(tag)
    return " / ".join(names) if names else tag_ref


def _distribution_mapping(raw_value: Any) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for item in _load_json_list(raw_value):
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        if not tag:
            continue
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            continue
        if probability <= 0:
            continue
        mapping[tag] = mapping.get(tag, 0.0) + probability
    total = sum(mapping.values())
    if total <= 0:
        return {}
    return {tag: probability / total for tag, probability in mapping.items()}


def _readable_tag_distribution(
    raw_value: Any,
    members_by_key: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    tag_probs = _distribution_mapping(raw_value)
    tags: list[dict[str, Any]] = []
    for tag, probability in sorted(tag_probs.items(), key=lambda item: item[1], reverse=True):
        tag_kind, cluster_key = _split_tag_ref(tag)
        tags.append(
            {
                "tag": tag,
                "kind": tag_kind,
                "cluster_key": cluster_key,
                "display": _tag_ref_display(tag, members_by_key),
                "probability": probability,
            }
        )
    return tags


def _scene_cluster_display_name(cluster_id: int, tags: list[dict[str, Any]]) -> str:
    names: list[str] = []
    for item in tags[:4]:
        for part in str(item.get("display") or "").split(" / "):
            if part and part not in names:
                names.append(part)
            if len(names) >= 3:
                break
        if len(names) >= 3:
            break
    return f"#{cluster_id}｜{' / '.join(names)}" if names else f"场景簇 #{cluster_id}"


def _build_behavior_graph_data(session: Any, session_id: Optional[str]) -> dict[str, Any]:
    statement = select(BehaviorSceneCluster)
    if session_id is not None and session_id != "all":
        if session_id == "__global__":
            statement = statement.where(BehaviorSceneCluster.session_id.is_(None))  # type: ignore[attr-defined]
        else:
            statement = statement.where(BehaviorSceneCluster.session_id == session_id)
    clusters = list(session.exec(statement.order_by(BehaviorSceneCluster.id)).all())  # type: ignore[attr-defined]
    cluster_ids = {cluster.id for cluster in clusters if cluster.id is not None}
    members_by_key = _tag_cluster_members_by_key(session)

    paths_by_cluster: dict[int, list[BehaviorExperiencePath]] = {cluster_id: [] for cluster_id in cluster_ids}
    if cluster_ids:
        paths = session.exec(
            select(BehaviorExperiencePath).where(col(BehaviorExperiencePath.scene_cluster_id).in_(cluster_ids))
        ).all()
        for path in paths:
            paths_by_cluster.setdefault(path.scene_cluster_id, []).append(path)

    scene_nodes: list[dict[str, Any]] = []
    scene_distribution_by_id: dict[int, dict[str, float]] = {}
    for cluster in clusters:
        if cluster.id is None:
            continue
        tags = _readable_tag_distribution(cluster.tag_distribution, members_by_key)
        label = _scene_cluster_display_name(cluster.id, tags)
        cluster_paths = paths_by_cluster.get(cluster.id, [])
        scene_distribution_by_id[cluster.id] = {str(item["tag"]): float(item["probability"]) for item in tags}
        scene_nodes.append(
            {
                "id": cluster.id,
                "label": label,
                "short_label": label.split("｜", 1)[1] if "｜" in label else label,
                "session_id": cluster.session_id or "__global__",
                "source_count": int(cluster.source_count or 0),
                "path_count": len(cluster_paths),
                "activation_count": sum(int(path.activation_count or 0) for path in cluster_paths),
                "success_count": sum(int(path.success_count or 0) for path in cluster_paths),
                "failure_count": sum(int(path.failure_count or 0) for path in cluster_paths),
                "update_time": _isoformat(cluster.update_time),
                "tags": tags,
            }
        )

    scene_edges: list[dict[str, Any]] = []
    scene_label_by_id = {node["id"]: node["label"] for node in scene_nodes}
    for left_id, right_id in combinations(scene_distribution_by_id, 2):
        left_tags = scene_distribution_by_id[left_id]
        right_tags = scene_distribution_by_id[right_id]
        shared_tags = sorted(set(left_tags) & set(right_tags))
        if not shared_tags:
            continue
        weight = sum(min(left_tags[tag], right_tags[tag]) for tag in shared_tags)
        if weight <= 0:
            continue
        scene_edges.append(
            {
                "source": left_id,
                "target": right_id,
                "source_label": scene_label_by_id.get(left_id, str(left_id)),
                "target_label": scene_label_by_id.get(right_id, str(right_id)),
                "weight": round(weight, 6),
                "shared_tags": [
                    {
                        "tag": tag,
                        "display": _tag_ref_display(tag, members_by_key),
                        "left": left_tags[tag],
                        "right": right_tags[tag],
                        "overlap": min(left_tags[tag], right_tags[tag]),
                    }
                    for tag in shared_tags
                ],
            }
        )
    scene_edges.sort(key=lambda item: item["weight"], reverse=True)

    tag_source_count: dict[str, int] = defaultdict(int)
    tag_aliases: dict[str, list[str]] = defaultdict(list)
    tag_kind_by_id: dict[str, str] = {}
    for cluster_key, members in members_by_key.items():
        for member in members:
            tag_kind = str(member["kind"] or "")
            tag_id = f"{tag_kind}:{cluster_key}" if tag_kind else cluster_key
            tag_kind_by_id[tag_id] = tag_kind
            tag_source_count[tag_id] += int(member["source_count"] or 0)
            tag = str(member["tag"] or "")
            if tag and tag not in tag_aliases[tag_id]:
                tag_aliases[tag_id].append(tag)

    tag_weight: dict[str, float] = defaultdict(float)
    tag_scene_count: dict[str, set[int]] = defaultdict(set)
    tag_edges: dict[tuple[str, str], dict[str, Any]] = {}
    for scene_id, tag_probs in scene_distribution_by_id.items():
        for tag, probability in tag_probs.items():
            tag_weight[tag] += probability
            tag_scene_count[tag].add(scene_id)
        for left_tag, right_tag in combinations(sorted(tag_probs), 2):
            edge_key = tuple(sorted((left_tag, right_tag)))
            edge = tag_edges.setdefault(edge_key, {"source": edge_key[0], "target": edge_key[1], "weight": 0.0, "count": 0})
            edge["weight"] += min(tag_probs[left_tag], tag_probs[right_tag])
            edge["count"] += 1

    tag_node_ids = set(tag_source_count) | set(tag_weight)
    tag_nodes = [
        {
            "id": tag_id,
            "kind": tag_kind_by_id.get(tag_id, _split_tag_ref(tag_id)[0]),
            "cluster_key": _split_tag_ref(tag_id)[1],
            "label": " / ".join(tag_aliases.get(tag_id, [])[:3]) or _tag_ref_display(tag_id, members_by_key),
            "aliases": tag_aliases.get(tag_id, [])[:12],
            "weight": round(tag_weight.get(tag_id, 0.0), 6),
            "scene_count": len(tag_scene_count.get(tag_id, set())),
            "source_count": tag_source_count.get(tag_id, 0),
        }
        for tag_id in sorted(tag_node_ids)
    ]
    tag_edges_payload = [
        {
            **edge,
            "weight": round(float(edge["weight"]), 6),
        }
        for edge in sorted(tag_edges.values(), key=lambda item: item["weight"], reverse=True)
        if float(edge["weight"]) > 0
    ]

    return {
        "scene_cluster_network": {
            "nodes": scene_nodes,
            "edges": scene_edges,
        },
        "tag_network": {
            "nodes": tag_nodes,
            "edges": tag_edges_payload,
        },
    }


def _chat_type_of(session: Optional[ChatSession]) -> str:
    if session is None:
        return ""
    return "group" if session.group_id else "private"


def _chat_display_name(session: Optional[ChatSession], session_id: Optional[str]) -> str:
    if session is None:
        return "全局行为" if not session_id else session_id
    if session.group_name:
        return session.group_name
    if session.user_nickname:
        return f"{session.user_nickname} 的私聊"
    return session.session_id


def _session_scope(session_id: Optional[str]) -> set[str]:
    normalized_session_id = str(session_id or "").strip()
    return {normalized_session_id} if normalized_session_id else set()


@router.get("/chats")
async def list_behavior_chats() -> dict[str, Any]:
    """列出存在行为经验路径的聊天流。"""

    with get_db_session(auto_commit=False) as session:
        path_rows = session.exec(
            select(
                BehaviorExperiencePath.session_id,
                func.count(BehaviorExperiencePath.id),
                func.max(BehaviorExperiencePath.last_active_time),
            )
            .group_by(BehaviorExperiencePath.session_id)
            .order_by(func.max(BehaviorExperiencePath.last_active_time).desc())
        ).all()
        cluster_rows = session.exec(
            select(BehaviorSceneCluster.session_id, func.count(BehaviorSceneCluster.id)).group_by(
                BehaviorSceneCluster.session_id
            )
        ).all()
        cluster_count_by_session = {row[0]: int(row[1] or 0) for row in cluster_rows}
        session_ids = [row[0] for row in path_rows if row[0]]
        chat_sessions = {}
        if session_ids:
            chat_sessions = {
                chat.session_id: chat
                for chat in session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(session_ids))).all()
            }

    chats = [
        BehaviorChatInfo(
            session_id=row[0] or "",
            display_name=_chat_display_name(chat_sessions.get(row[0]), row[0]),
            platform=str(chat_sessions[row[0]].platform) if row[0] in chat_sessions else "",
            account_id=chat_sessions[row[0]].account_id if row[0] in chat_sessions else None,
            chat_type=_chat_type_of(chat_sessions.get(row[0])),
            path_count=int(row[1] or 0),
            cluster_count=cluster_count_by_session.get(row[0], 0),
            scene_count=cluster_count_by_session.get(row[0], 0),
            last_active_time=_isoformat(row[2]),
        ).model_dump()
        for row in path_rows
    ]
    return {"success": True, "data": chats}


@router.get("/paths", response_model=BehaviorPathListResponse)
async def list_behavior_paths(
    session_id: Annotated[Optional[str], Query()] = None,
    search: Annotated[str, Query()] = "",
    enabled: Annotated[str, Query()] = "all",
    actor_type: Annotated[str, Query()] = "all",
    learning_type: Annotated[str, Query()] = "all",
    sort_by: Annotated[str, Query()] = "update_time",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> BehaviorPathListResponse:
    """分页列出行为经验路径。"""

    normalized_search = " ".join(str(search or "").split()).strip().lower()
    with get_db_session(auto_commit=False) as session:
        statement = select(BehaviorExperiencePath)
        if session_id is not None and session_id != "all":
            if session_id == "__global__":
                statement = statement.where(BehaviorExperiencePath.session_id.is_(None))  # type: ignore[attr-defined]
            else:
                statement = statement.where(BehaviorExperiencePath.session_id == session_id)
        if enabled == "true":
            statement = statement.where(BehaviorExperiencePath.enabled.is_(True))  # type: ignore[attr-defined]
        elif enabled == "false":
            statement = statement.where(BehaviorExperiencePath.enabled.is_(False))  # type: ignore[attr-defined]
        if actor_type != "all":
            statement = statement.where(BehaviorExperiencePath.actor_type == actor_type)
        if learning_type != "all":
            statement = statement.where(BehaviorExperiencePath.learning_type == learning_type)

        paths = list(session.exec(statement).all())
        path_items = _build_path_items(session, paths)
        if normalized_search:
            path_items = [
                item
                for item in path_items
                if normalized_search
                in (
                    f"{item.scene_cluster_name}\n{item.action}\n{item.outcome}\n"
                    f"{item.actor_type}\n{item.learning_type}\n{item.chat_name}\n"
                    + "\n".join(f"{tag.tag}\n{tag.display}" for tag in item.scene_cluster_tags)
                ).lower()
            ]
        path_items = _sort_behavior_path_items(path_items, sort_by=sort_by, sort_order=sort_order)
        total = len(path_items)
        start = (page - 1) * page_size
        data = path_items[start : start + page_size]
    return BehaviorPathListResponse(total=total, page=page, page_size=page_size, data=data)


@router.get("/clusters", response_model=BehaviorClusterListResponse)
async def list_behavior_clusters(
    session_id: Annotated[Optional[str], Query()] = None,
    search: Annotated[str, Query()] = "",
    sort_by: Annotated[str, Query()] = "update_time",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=5000)] = 20,
) -> BehaviorClusterListResponse:
    """分页列出行为场景簇，便于浏览 tag 概率分布。"""

    normalized_search = " ".join(str(search or "").split()).strip().lower()
    with get_db_session(auto_commit=False) as session:
        statement = select(BehaviorSceneCluster)
        if session_id is not None and session_id != "all":
            if session_id == "__global__":
                statement = statement.where(BehaviorSceneCluster.session_id.is_(None))  # type: ignore[attr-defined]
            else:
                statement = statement.where(BehaviorSceneCluster.session_id == session_id)

        clusters = list(session.exec(statement.order_by(BehaviorSceneCluster.update_time.desc())).all())  # type: ignore[attr-defined]
        cluster_items = _build_cluster_items(session, clusters)
        if normalized_search:
            cluster_items = [
                item
                for item in cluster_items
                if normalized_search
                in (
                    f"{item.name}\n{item.chat_name}\n"
                    + "\n".join(f"{tag.tag}\n{tag.display}" for tag in item.tags)
                ).lower()
            ]
        cluster_items = _sort_behavior_cluster_items(cluster_items, sort_by=sort_by, sort_order=sort_order)
        total = len(cluster_items)
        start = (page - 1) * page_size
        data = cluster_items[start : start + page_size]
    return BehaviorClusterListResponse(total=total, page=page, page_size=page_size, data=data)


@router.get("/graph-data", response_model=BehaviorGraphDataResponse)
async def get_behavior_graph_data(
    session_id: Annotated[Optional[str], Query()] = None,
) -> BehaviorGraphDataResponse:
    """返回 WebUI 行为学习图谱所需的场景簇网络和 tag 簇分布网络。"""

    with get_db_session(auto_commit=False) as session:
        return BehaviorGraphDataResponse(data=_build_behavior_graph_data(session, session_id))


@router.get("/paths/{path_id}", response_model=BehaviorPathDetailResponse)
async def get_behavior_path_detail(path_id: int) -> BehaviorPathDetailResponse:
    """读取一条行为经验路径及其局部图谱。"""

    with get_db_session(auto_commit=False) as session:
        path = session.get(BehaviorExperiencePath, path_id)
        if path is None:
            raise HTTPException(status_code=404, detail="行为经验路径不存在")
        item = _build_path_items(session, [path])[0]
        members_by_key = _tag_cluster_members_by_key(session)
        scene_cluster = _cluster_payload(
            session.get(BehaviorSceneCluster, path.scene_cluster_id),
            members_by_key,
        )
    nodes: list[BehaviorNodePayload] = [
        BehaviorNodePayload(id=path.action_id, kind="action", label=item.action),
        BehaviorNodePayload(id=path.outcome_id, kind="outcome", label=item.outcome),
    ]
    return BehaviorPathDetailResponse(
        data={
            "path": item.model_dump(),
            "scene_cluster": scene_cluster.model_dump(),
            "evidence": _load_json_list(path.evidence_list),
            "feedback": _load_json_list(path.feedback_list),
            "nodes": [node.model_dump() for node in nodes],
            "edges": [],
        }
    )


@router.post("/retrieval-debug")
async def debug_behavior_retrieval(request: BehaviorScenarioDebugRequest) -> dict[str, Any]:
    """按输入场景模拟一次本地场景簇检索。"""

    scene_text = " ".join(request.scene_text.split()).strip()
    if scene_text:
        profile = await _analyze_debug_scene_text(scene_text)
        input_mode = "llm_scene_text"
        error = "" if profile.has_signal else "LLM 没有生成有效场景 tag 簇，请调整一句话场景描述后重试。"
    else:
        profile = BehaviorScenarioProfile(
            summary=" ".join(request.summary.split()).strip(),
            tag_clusters=parse_behavior_scenario_response(
                json.dumps(
                    {
                        "tag_clusters": request.tag_clusters,
                        "need": request.need,
                        "other_traits": request.other_traits,
                    },
                    ensure_ascii=False,
                )
            ).tag_clusters,
            confidence=1.0 if request.tag_clusters or request.need or request.other_traits else 0.0,
        )
        input_mode = "manual_tags"
        error = ""
    debug_payload = debug_retrieve_behavior_scores_from_scene_clusters(
        session_ids=_session_scope(request.session_id),
        include_global=request.include_global,
        profile=profile,
        max_count=request.max_count,
        retrieval_mode=request.retrieval_mode,
    )
    behavior_ids = [item["behavior_id"] for item in debug_payload.get("candidate_scores", [])]
    with get_db_session(auto_commit=False) as session:
        paths = []
        if behavior_ids:
            paths = session.exec(select(BehaviorExperiencePath).where(col(BehaviorExperiencePath.id).in_(behavior_ids))).all()
        path_items = {item.id: item for item in _build_path_items(session, list(paths))}
        matched_clusters = _enrich_debug_clusters(session, debug_payload.get("matched_clusters", []))
    return {
        "success": True,
        "data": {
            **debug_payload,
            "input_mode": input_mode,
            "scenario_profile": _profile_debug_payload(profile),
            "error": error or debug_payload.get("error", ""),
            "matched_clusters": matched_clusters,
            "candidates": [
                {
                    **score_item,
                    "path": path_items.get(score_item["behavior_id"]).model_dump()
                    if score_item["behavior_id"] in path_items
                    else None,
                }
                for score_item in debug_payload.get("candidate_scores", [])
            ],
        },
    }


def _sort_behavior_path_items(
    items: list[BehaviorPathItem],
    *,
    sort_by: str,
    sort_order: str,
) -> list[BehaviorPathItem]:
    normalized_sort_by = str(sort_by or "update_time").strip()
    normalized_sort_order = str(sort_order or "desc").strip().lower()
    reverse = normalized_sort_order != "asc"
    text_fields = {"action", "chat_name", "outcome", "scene_cluster_name"}
    time_fields = {"last_active_time", "last_feedback_time", "update_time"}
    number_fields = {
        "activation_count",
        "count",
        "failure_count",
        "scene_cluster_source_count",
        "score",
        "success_count",
    }
    allowed_fields = text_fields | time_fields | number_fields
    if normalized_sort_by not in allowed_fields:
        normalized_sort_by = "update_time"

    if normalized_sort_by in text_fields | time_fields:
        return sorted(items, key=lambda item: str(getattr(item, normalized_sort_by) or ""), reverse=reverse)
    return sorted(items, key=lambda item: float(getattr(item, normalized_sort_by) or 0), reverse=reverse)


def _sort_behavior_cluster_items(
    items: list[BehaviorClusterItem],
    *,
    sort_by: str,
    sort_order: str,
) -> list[BehaviorClusterItem]:
    normalized_sort_by = str(sort_by or "").strip()
    reverse = str(sort_order or "").strip().lower() != "asc"
    text_fields = {"chat_name", "name", "session_id"}
    time_fields = {"last_active_time", "update_time"}
    number_fields = {
        "activation_count",
        "enabled_path_count",
        "failure_count",
        "observed_path_count",
        "path_count",
        "self_reflection_path_count",
        "source_count",
        "success_count",
    }
    allowed_fields = text_fields | time_fields | number_fields
    if normalized_sort_by not in allowed_fields:
        normalized_sort_by = "update_time"

    if normalized_sort_by in text_fields | time_fields:
        return sorted(items, key=lambda item: str(getattr(item, normalized_sort_by) or ""), reverse=reverse)
    return sorted(items, key=lambda item: float(getattr(item, normalized_sort_by) or 0), reverse=reverse)


def _build_path_items(session: Any, paths: list[BehaviorExperiencePath]) -> list[BehaviorPathItem]:
    cluster_ids = {path.scene_cluster_id for path in paths}
    action_ids = {path.action_id for path in paths}
    outcome_ids = {path.outcome_id for path in paths}
    session_ids = {path.session_id for path in paths if path.session_id}
    scene_clusters = _load_scene_clusters(session, cluster_ids)
    action_nodes = {
        node.id: node
        for node in session.exec(select(BehaviorAction).where(col(BehaviorAction.id).in_(action_ids))).all()
    }
    outcome_nodes = {
        node.id: node
        for node in session.exec(select(BehaviorOutcome).where(col(BehaviorOutcome.id).in_(outcome_ids))).all()
    }
    chat_sessions = {
        chat.session_id: chat
        for chat in session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(session_ids))).all()
    }
    members_by_key = _tag_cluster_members_by_key(session)
    items: list[BehaviorPathItem] = []
    for path in paths:
        scene_cluster = scene_clusters.get(path.scene_cluster_id)
        cluster_payload = _cluster_payload(scene_cluster, members_by_key)
        cluster_name = cluster_payload.name
        items.append(
            BehaviorPathItem(
                id=path.id or 0,
                session_id=path.session_id,
                chat_name=_chat_display_name(chat_sessions.get(path.session_id), path.session_id),
                scene_cluster_id=cluster_payload.id,
                scene_cluster_name=cluster_name,
                scene_cluster_tags=cluster_payload.tags,
                scene_cluster_source_count=cluster_payload.source_count,
                actor_type=str(path.actor_type or "other_user"),
                learning_type=str(path.learning_type or "observed_behavior"),
                action=action_nodes[path.action_id].action if path.action_id in action_nodes else "",
                outcome=outcome_nodes[path.outcome_id].outcome if path.outcome_id in outcome_nodes else "",
                count=int(path.count or 0),
                activation_count=int(path.activation_count or 0),
                success_count=int(path.success_count or 0),
                failure_count=int(path.failure_count or 0),
                score=float(path.score or 0.0),
                enabled=bool(path.enabled),
                last_active_time=_isoformat(path.last_active_time),
                last_feedback_time=_isoformat(path.last_feedback_time),
                update_time=_isoformat(path.update_time),
            )
        )
    return items


def _enrich_debug_clusters(session: Any, matched_clusters: Any) -> list[dict[str, Any]]:
    if not isinstance(matched_clusters, list):
        return []
    cluster_ids = {
        int(item["cluster_id"])
        for item in matched_clusters
        if isinstance(item, dict) and isinstance(item.get("cluster_id"), int)
    }
    scene_clusters = _load_scene_clusters(session, cluster_ids)
    members_by_key = _tag_cluster_members_by_key(session)
    enriched_clusters: list[dict[str, Any]] = []
    for item in matched_clusters:
        if not isinstance(item, dict):
            continue
        cluster_id = item.get("cluster_id")
        cluster = scene_clusters.get(cluster_id) if isinstance(cluster_id, int) else None
        cluster_payload = _cluster_payload(cluster, members_by_key)
        enriched_clusters.append(
            {
                **item,
                "name": cluster_payload.name or str(item.get("name") or ""),
                "tags": [tag.model_dump() for tag in cluster_payload.tags],
                "source_count": cluster_payload.source_count,
            }
        )
    return enriched_clusters


def _build_cluster_items(session: Any, clusters: list[BehaviorSceneCluster]) -> list[BehaviorClusterItem]:
    cluster_ids = {cluster.id for cluster in clusters if cluster.id is not None}
    session_ids = {cluster.session_id for cluster in clusters if cluster.session_id}
    paths_by_cluster_id: dict[int, list[BehaviorExperiencePath]] = {cluster_id: [] for cluster_id in cluster_ids}
    if cluster_ids:
        paths = session.exec(
            select(BehaviorExperiencePath).where(col(BehaviorExperiencePath.scene_cluster_id).in_(cluster_ids))
        ).all()
        for path in paths:
            paths_by_cluster_id.setdefault(path.scene_cluster_id, []).append(path)
    chat_sessions = {
        chat.session_id: chat
        for chat in session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(session_ids))).all()
    }
    members_by_key = _tag_cluster_members_by_key(session)

    items: list[BehaviorClusterItem] = []
    for cluster in clusters:
        cluster_payload = _cluster_payload(cluster, members_by_key)
        cluster_paths = paths_by_cluster_id.get(cluster.id or 0, [])
        last_active_time = max((path.last_active_time for path in cluster_paths if path.last_active_time), default=None)
        items.append(
            BehaviorClusterItem(
                **cluster_payload.model_dump(),
                session_id=cluster.session_id,
                chat_name=_chat_display_name(chat_sessions.get(cluster.session_id), cluster.session_id),
                path_count=len(cluster_paths),
                enabled_path_count=sum(1 for path in cluster_paths if path.enabled),
                activation_count=sum(int(path.activation_count or 0) for path in cluster_paths),
                success_count=sum(int(path.success_count or 0) for path in cluster_paths),
                failure_count=sum(int(path.failure_count or 0) for path in cluster_paths),
                observed_path_count=sum(1 for path in cluster_paths if path.learning_type == "observed_behavior"),
                self_reflection_path_count=sum(1 for path in cluster_paths if path.learning_type == "self_reflection"),
                last_active_time=_isoformat(last_active_time),
            )
        )
    return items


def _load_scene_clusters(session: Any, scene_cluster_ids: set[int]) -> dict[int, BehaviorSceneCluster]:
    if not scene_cluster_ids:
        return {}
    return {
        cluster.id: cluster
        for cluster in session.exec(select(BehaviorSceneCluster).where(col(BehaviorSceneCluster.id).in_(scene_cluster_ids))).all()
        if cluster.id is not None
    }

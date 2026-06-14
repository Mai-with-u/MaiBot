from datetime import datetime
from typing import Any, Optional, Sequence

from sqlmodel import Session, select

import json

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
)
from src.common.logger import get_logger

from .behavior_scenario import BehaviorScenarioProfile
from .behavior_scene_cluster_store import (
    _load_tag_cluster_lookup,
    apply_behavior_scene_feedback,
    build_profile_tag_distribution,
    mark_behavior_scene_links_selected,
    upsert_behavior_graph_refs,
)

logger = get_logger("behavior_pattern_store")

ACTOR_OTHER_USER = "other_user"
ACTOR_MAIBOT_SELF = "maibot_self"
ACTOR_GROUP_COLLECTIVE = "group_collective"
ACTOR_UNKNOWN = "unknown"
LEARNING_OBSERVED = "observed_behavior"
LEARNING_SELF_REFLECTION = "self_reflection"
ALLOWED_ACTOR_TYPES = {ACTOR_OTHER_USER, ACTOR_MAIBOT_SELF, ACTOR_GROUP_COLLECTIVE, ACTOR_UNKNOWN}
ALLOWED_LEARNING_TYPES = {LEARNING_OBSERVED, LEARNING_SELF_REFLECTION}
EVIDENCE_HISTORY_LIMIT = 20
FEEDBACK_HISTORY_LIMIT = 30
MIN_BEHAVIOR_SCORE = -6.0
MAX_BEHAVIOR_SCORE = 8.0
NEGATIVE_FEEDBACK_STATUSES = {"failed", "blocked", "abandoned"}
POSITIVE_FEEDBACK_STATUSES = {"success", "succeeded", "completed"}
PARTIAL_POSITIVE_FEEDBACK_STATUSES = {"partial_success"}


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


def _dump_json_list(items: Sequence[Any]) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def _clamp_score(score: float) -> float:
    return min(MAX_BEHAVIOR_SCORE, max(MIN_BEHAVIOR_SCORE, score))


def _normalize_text(text: str, *, max_length: int = 240) -> str:
    normalized_text = " ".join(str(text or "").split()).strip()
    if len(normalized_text) <= max_length:
        return normalized_text
    return normalized_text[:max_length].rstrip()


def _normalize_source_ids(source_ids: Sequence[str]) -> list[str]:
    normalized_ids: list[str] = []
    for source_id in source_ids:
        normalized_id = str(source_id or "").strip()
        if not normalized_id or normalized_id in normalized_ids:
            continue
        normalized_ids.append(normalized_id)
    return normalized_ids


def _coerce_actor_type(actor_type: str) -> str:
    normalized_actor_type = str(actor_type or "").strip().lower()
    if normalized_actor_type in ALLOWED_ACTOR_TYPES:
        return normalized_actor_type
    return ACTOR_UNKNOWN


def _coerce_learning_type(learning_type: str, *, actor_type: str) -> str:
    normalized_learning_type = str(learning_type or "").strip().lower()
    if normalized_learning_type in ALLOWED_LEARNING_TYPES:
        return normalized_learning_type
    return LEARNING_SELF_REFLECTION if actor_type == ACTOR_MAIBOT_SELF else LEARNING_OBSERVED


def _build_evidence_item(
    *,
    action: str,
    outcome: str,
    source_ids: Sequence[str],
    actor_type: str,
    learning_type: str,
    profile_tag_distribution: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    evidence_item = {
        "action": action,
        "outcome": outcome,
        "source_ids": _normalize_source_ids(source_ids),
        "actor_type": actor_type,
        "learning_type": learning_type,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if profile_tag_distribution:
        evidence_item["profile_tag_distribution"] = list(profile_tag_distribution)
    return evidence_item


def _merge_profile_tag_distribution_from_evidence(evidence_list: Any) -> list[dict[str, float | str]]:
    tag_totals: dict[str, float] = {}
    distribution_count = 0
    for evidence_item in _load_json_list(evidence_list):
        if not isinstance(evidence_item, dict):
            continue
        raw_distribution = evidence_item.get("profile_tag_distribution")
        if not isinstance(raw_distribution, list):
            continue
        local_tags: dict[str, float] = {}
        for item in raw_distribution:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip()
            if not tag:
                continue
            try:
                probability = float(item.get("probability") or 0.0)
            except (TypeError, ValueError):
                continue
            if probability > 0:
                local_tags[tag] = local_tags.get(tag, 0.0) + probability
        if not local_tags:
            continue
        distribution_count += 1
        for tag, probability in local_tags.items():
            tag_totals[tag] = tag_totals.get(tag, 0.0) + probability
    if distribution_count <= 0:
        return []
    averaged_tags = {tag: probability / float(distribution_count) for tag, probability in tag_totals.items()}
    total_probability = sum(averaged_tags.values())
    if total_probability <= 0:
        return []
    return [
        {
            "tag": tag,
            "probability": round(probability / total_probability, 6),
        }
        for tag, probability in sorted(averaged_tags.items())
    ]


def _path_texts_from_session(
    session: Session,
    path: BehaviorExperiencePath,
) -> tuple[str, str]:
    action = session.get(BehaviorAction, path.action_id)
    outcome = session.get(BehaviorOutcome, path.outcome_id)
    action_text = action.action if action is not None else ""
    outcome_text = outcome.outcome if outcome is not None else ""
    return action_text, outcome_text


def _path_to_dict_from_session(
    session: Session,
    path: BehaviorExperiencePath,
) -> dict[str, Any]:
    action, outcome = _path_texts_from_session(session, path)
    return {
        "id": path.id,
        "action": action,
        "outcome": outcome,
        "scene_cluster_id": path.scene_cluster_id,
        "action_id": path.action_id,
        "outcome_id": path.outcome_id,
        "actor_type": path.actor_type,
        "learning_type": path.learning_type,
        "count": path.count,
        "activation_count": path.activation_count,
        "success_count": path.success_count,
        "failure_count": path.failure_count,
        "score": path.score,
        "enabled": path.enabled,
        "session_id": path.session_id,
        "profile_tag_distribution": _merge_profile_tag_distribution_from_evidence(path.evidence_list),
        "last_active_time": path.last_active_time.isoformat() if path.last_active_time else "",
        "last_feedback_time": path.last_feedback_time.isoformat() if path.last_feedback_time else "",
    }


def behavior_experience_to_dict(path: BehaviorExperiencePath) -> dict[str, Any]:
    if path.id is None:
        return {
            "id": None,
            "action": "",
            "outcome": "",
            "actor_type": path.actor_type,
            "learning_type": path.learning_type,
            "count": path.count,
            "activation_count": path.activation_count,
            "success_count": path.success_count,
            "failure_count": path.failure_count,
            "score": path.score,
            "enabled": path.enabled,
            "session_id": path.session_id,
            "profile_tag_distribution": [],
            "last_active_time": path.last_active_time.isoformat() if path.last_active_time else "",
            "last_feedback_time": path.last_feedback_time.isoformat() if path.last_feedback_time else "",
        }

    try:
        with get_db_session(auto_commit=False) as session:
            attached_path = session.get(BehaviorExperiencePath, path.id)
            if attached_path is None:
                return {}
            return _path_to_dict_from_session(session, attached_path)
    except Exception as exc:
        logger.error(f"读取行为经验路径文本失败: id={path.id} error={exc}")
        return {}


def upsert_behavior_experience(
    *,
    action: str,
    outcome: str,
    source_ids: Sequence[str],
    session_id: str,
    scenario_profile: BehaviorScenarioProfile,
    scene_start: str,
    actor_type: str = ACTOR_OTHER_USER,
    learning_type: str = LEARNING_OBSERVED,
) -> Optional[BehaviorExperiencePath]:
    normalized_action = _normalize_text(action, max_length=240)
    normalized_outcome = _normalize_text(outcome, max_length=240)
    normalized_source_ids = _normalize_source_ids(source_ids)
    normalized_actor_type = _coerce_actor_type(actor_type)
    normalized_learning_type = _coerce_learning_type(learning_type, actor_type=normalized_actor_type)
    if not normalized_action or not normalized_outcome:
        logger.warning(
            "跳过写入行为经验路径：归一化后字段为空 "
            f"action={normalized_action!r} outcome={normalized_outcome!r}"
        )
        return None

    try:
        with get_db_session() as session:
            graph_refs = upsert_behavior_graph_refs(
                session=session,
                session_id=session_id,
                profile=scenario_profile,
                scene_start=scene_start,
                action=normalized_action,
                outcome=normalized_outcome,
            )
            if graph_refs is None:
                logger.warning(
                    "跳过写入行为经验路径：场景簇引用生成失败 "
                    f"session_id={session_id} action={normalized_action} outcome={normalized_outcome}"
                )
                return None

            profile_tag_distribution = build_profile_tag_distribution(
                scenario_profile,
                tag_lookup=_load_tag_cluster_lookup(session),
            )
            now = datetime.now()
            evidence_item = _build_evidence_item(
                action=normalized_action,
                outcome=normalized_outcome,
                source_ids=normalized_source_ids,
                actor_type=normalized_actor_type,
                learning_type=normalized_learning_type,
                profile_tag_distribution=profile_tag_distribution,
            )

            statement = (
                select(BehaviorExperiencePath)
                .where(BehaviorExperiencePath.session_id == session_id)
                .where(BehaviorExperiencePath.scene_cluster_id == graph_refs.scene_cluster_id)
                .where(BehaviorExperiencePath.action_id == graph_refs.action_id)
                .where(BehaviorExperiencePath.outcome_id == graph_refs.outcome_id)
                .where(BehaviorExperiencePath.actor_type == normalized_actor_type)
                .where(BehaviorExperiencePath.learning_type == normalized_learning_type)
            )
            path = session.exec(statement).first()
            if path is None:
                path = BehaviorExperiencePath(
                    session_id=session_id,
                    scene_cluster_id=graph_refs.scene_cluster_id,
                    action_id=graph_refs.action_id,
                    outcome_id=graph_refs.outcome_id,
                    actor_type=normalized_actor_type,
                    learning_type=normalized_learning_type,
                    evidence_list=_dump_json_list([evidence_item]),
                    feedback_list=_dump_json_list([]),
                    count=1,
                    activation_count=0,
                    success_count=0,
                    failure_count=0,
                    score=0.0,
                    enabled=True,
                    last_active_time=now,
                    create_time=now,
                    update_time=now,
                )
            else:
                evidence_items = _load_json_list(path.evidence_list)
                evidence_items.append(evidence_item)
                path.evidence_list = _dump_json_list(evidence_items[-EVIDENCE_HISTORY_LIMIT:])
                path.count += 1
                path.last_active_time = now
                path.update_time = now

            session.add(path)
            session.flush()
            session.refresh(path)
            session.expunge(path)
            return path
    except Exception as exc:
        logger.exception(
            "写入行为经验路径失败: "
            f"session_id={session_id} action={normalized_action} outcome={normalized_outcome} "
            f"source_ids={normalized_source_ids} error={exc}"
        )
        return None


def list_behavior_experiences_for_sessions(
    *,
    session_ids: set[str],
    include_global: bool = False,
    min_score: float = -4.0,
) -> list[BehaviorExperiencePath]:
    try:
        with get_db_session(auto_commit=False) as session:
            statement = select(BehaviorExperiencePath).where(BehaviorExperiencePath.enabled.is_(True))  # type: ignore[attr-defined]
            statement = statement.where(BehaviorExperiencePath.score >= min_score)
            if include_global:
                pass
            elif session_ids:
                statement = statement.where(
                    (BehaviorExperiencePath.session_id.in_(session_ids))  # type: ignore[attr-defined]
                    | (BehaviorExperiencePath.session_id.is_(None))  # type: ignore[attr-defined]
                )
            else:
                statement = statement.where(BehaviorExperiencePath.session_id.is_(None))  # type: ignore[attr-defined]
            paths = session.exec(statement).all()
            for path in paths:
                session.expunge(path)
            return list(paths)
    except Exception as exc:
        logger.error(f"读取行为经验路径候选失败: {exc}")
        return []


def get_behavior_experience(path_id: int) -> Optional[BehaviorExperiencePath]:
    if path_id <= 0:
        return None
    try:
        with get_db_session(auto_commit=False) as session:
            path = session.get(BehaviorExperiencePath, path_id)
            if path is not None:
                session.expunge(path)
            return path
    except Exception as exc:
        logger.error(f"读取行为经验路径失败: id={path_id} error={exc}")
        return None


def mark_behavior_experience_selected(path_id: int) -> Optional[BehaviorExperiencePath]:
    if path_id <= 0:
        return None
    now = datetime.now()
    try:
        with get_db_session() as session:
            path = session.get(BehaviorExperiencePath, path_id)
            if path is None:
                return None
            path.activation_count += 1
            path.last_active_time = now
            path.update_time = now
            session.add(path)
            session.flush()
            session.refresh(path)
            session.expunge(path)
            selected_path = path
    except Exception as exc:
        logger.error(f"更新行为经验路径激活状态失败: id={path_id} error={exc}")
        return None

    mark_behavior_scene_links_selected(path_id)
    return selected_path


def apply_behavior_feedback(
    *,
    pattern_id: int,
    score_delta: float,
    status: str,
    reason: str,
    outcome: str,
    session_id: str,
    source_ids: Sequence[str] = (),
) -> Optional[BehaviorExperiencePath]:
    normalized_status = str(status or "").strip().lower()
    normalized_reason = _normalize_text(reason, max_length=300)
    normalized_outcome = _normalize_text(outcome, max_length=240)
    normalized_source_ids = _normalize_source_ids(source_ids)
    now = datetime.now()

    try:
        with get_db_session() as session:
            path = session.get(BehaviorExperiencePath, pattern_id)
            if path is None:
                return None

            feedback_items = _load_json_list(path.feedback_list)
            feedback_items.append(
                {
                    "score_delta": float(score_delta),
                    "status": normalized_status,
                    "reason": normalized_reason,
                    "outcome": normalized_outcome,
                    "session_id": session_id,
                    "source_ids": normalized_source_ids,
                    "created_at": now.isoformat(timespec="seconds"),
                }
            )
            path.feedback_list = _dump_json_list(feedback_items[-FEEDBACK_HISTORY_LIMIT:])
            path.score = _clamp_score(float(path.score or 0.0) + float(score_delta))
            path.last_feedback_time = now
            path.update_time = now
            if normalized_status in POSITIVE_FEEDBACK_STATUSES:
                path.success_count += 1
            elif normalized_status in PARTIAL_POSITIVE_FEEDBACK_STATUSES:
                pass
            elif normalized_status in NEGATIVE_FEEDBACK_STATUSES:
                path.failure_count += 1
            if path.score <= MIN_BEHAVIOR_SCORE and path.failure_count >= 3:
                path.enabled = False

            session.add(path)
            session.flush()
            session.refresh(path)
            session.expunge(path)
            feedback_path = path
    except Exception as exc:
        logger.error(f"写入行为经验路径反馈失败: id={pattern_id} error={exc}")
        return None

    apply_behavior_scene_feedback(
        experience_path_id=pattern_id,
        score_delta=score_delta,
        status=normalized_status,
    )
    return feedback_path


# 兼容旧调用命名；运行时实体已经是 BehaviorExperiencePath。
behavior_pattern_to_dict = behavior_experience_to_dict
upsert_behavior_pattern = upsert_behavior_experience
list_behavior_patterns_for_sessions = list_behavior_experiences_for_sessions
get_behavior_pattern = get_behavior_experience
mark_behavior_pattern_selected = mark_behavior_experience_selected

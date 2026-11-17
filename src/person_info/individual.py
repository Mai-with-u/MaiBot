"""统一人物资料、来源追溯和经预览确认的个体合并。"""

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlmodel import Session, col, select

import hashlib
import json

from src.common.database.database_model import Individual, IndividualMergeEvent, PersonInfo

ProfileField = Literal["display_name", "is_known"]


class IndividualProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=255)
    is_known: StrictBool


class IndividualProfileSource(IndividualProfile):
    individual_id: str
    origins: List[Dict[str, Any]]


class IndividualMergePreview(BaseModel):
    target_individual_id: str
    revision: str
    profile: IndividualProfile
    conflicts: List[ProfileField]
    sources: List[IndividualProfileSource]
    members: Dict[str, str]


def profile_of(individual: Individual) -> IndividualProfile:
    return IndividualProfile(
        display_name=individual.display_name, is_known=individual.is_known
    )


def get_individual_members(session: Session, individual_id: str) -> List[PersonInfo]:
    if session.get(Individual, individual_id) is None:
        raise LookupError("个体不存在")
    return list(
        session.exec(select(PersonInfo).where(PersonInfo.individual_id == individual_id).order_by(PersonInfo.id)).all()
    )


def preview_individual_merge(
    session: Session,
    *,
    target_individual_id: str,
    individual_ids: List[str],
) -> IndividualMergePreview:
    ids = set(individual_ids)
    if not ids or target_individual_id not in ids:
        raise ValueError("合并目标必须属于所选人物")
    individuals = list(
        session.exec(
            select(Individual).where(col(Individual.individual_id).in_(ids)).order_by(Individual.individual_id)
        ).all()
    )
    if len(individuals) != len(ids):
        raise LookupError("人物已不存在，请重新选择")
    people = list(
        session.exec(
            select(PersonInfo).where(col(PersonInfo.individual_id).in_(ids)).order_by(PersonInfo.person_id)
        ).all()
    )
    if {person.individual_id for person in people} != ids:
        raise ValueError("不能合并没有账号的人物")
    sources = [
        IndividualProfileSource(
            **profile_of(individual).model_dump(),
            individual_id=individual.individual_id,
            origins=json.loads(individual.profile_sources_json),
        )
        for individual in individuals
    ]
    members = {person.person_id: person.individual_id for person in people}
    revision_data = {
        "target": target_individual_id,
        "sources": [source.model_dump() for source in sources],
        "members": [
            {
                "person_id": person.person_id,
                "individual_id": person.individual_id,
                "platform": person.platform,
                "user_id": person.user_id,
                "nickname": person.user_nickname,
                "group_cardname": person.group_cardname,
            }
            for person in people
        ],
    }
    revision = hashlib.sha256(json.dumps(revision_data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    final = profile_of(
        next(individual for individual in individuals if individual.individual_id == target_individual_id)
    )
    conflicts: List[ProfileField] = []
    if len({source.display_name for source in sources}) > 1:
        conflicts.append("display_name")
    if len({source.is_known for source in sources}) > 1:
        conflicts.append("is_known")
    return IndividualMergePreview(
        target_individual_id=target_individual_id,
        revision=revision,
        profile=final,
        conflicts=conflicts,
        sources=sources,
        members=members,
    )


def merge_individuals(
    session: Session,
    *,
    target_individual_id: str,
    expected_members: Dict[str, str],
    expected_revision: str,
    profile: IndividualProfile,
    resolved_conflicts: List[ProfileField],
) -> Individual:
    """调用方在读取前取得写事务，按同一份预览原子保存最终资料及全部来源。"""
    source_ids = set(expected_members.values())
    if len(source_ids) < 2:
        raise ValueError("请选择至少两个不同的人物")
    try:
        preview = preview_individual_merge(
            session, target_individual_id=target_individual_id, individual_ids=sorted(source_ids)
        )
    except LookupError as exc:
        raise ValueError("人物资料或成员已变化，请重新预览并确认合并") from exc
    if preview.members != expected_members or preview.revision != expected_revision:
        raise ValueError("人物资料或成员已变化，请重新预览并确认合并")
    missing = set(preview.conflicts) - set(resolved_conflicts)
    if missing:
        raise ValueError("请先处理全部资料冲突：" + ", ".join(sorted(missing)))
    target = session.get(Individual, target_individual_id)
    if target is None:
        raise LookupError("目标人物不存在")
    # 所有来源保留，不把被选中的最终值误当成唯一历史来源。
    origins: List[Dict[str, Any]] = []
    seen = set()
    for source in preview.sources:
        for origin in source.origins:
            key = json.dumps(origin, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                origins.append(origin)
                seen.add(key)
    session.add(
        IndividualMergeEvent(
            target_individual_id=target_individual_id,
            members_json=json.dumps(preview.members, ensure_ascii=False, sort_keys=True),
            profiles_json=json.dumps(
                {
                    "before": [source.model_dump() for source in preview.sources],
                    "after": profile.model_dump(),
                    "resolved_conflicts": resolved_conflicts,
                    "revision": preview.revision,
                },
                ensure_ascii=False,
            ),
        )
    )
    target.display_name = profile.display_name
    target.is_known = profile.is_known
    target.profile_sources_json = json.dumps(origins, ensure_ascii=False)
    session.add(target)
    for person in session.exec(select(PersonInfo).where(col(PersonInfo.individual_id).in_(source_ids))).all():
        person.individual_id = target_individual_id
        person.individual = target
        session.add(person)
    session.flush()
    for source_id in source_ids - {target_individual_id}:
        source = session.get(Individual, source_id)
        if source is None:
            raise LookupError("来源人物不存在")
        session.delete(source)
    session.flush()
    return target

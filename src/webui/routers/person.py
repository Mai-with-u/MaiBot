"""人物信息管理 API 路由"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, text
from sqlmodel import col, delete, select

import json

from src.common.database.database import get_db_session
from src.common.database.database_model import Individual, PersonInfo
from src.common.logger import get_logger
from src.person_info.individual import (
    IndividualMergePreview,
    IndividualProfile,
    ProfileField,
    get_individual_members,
    merge_individuals,
    preview_individual_merge,
    profile_of,
)

logger = get_logger("webui.person")

# 创建路由器
router = APIRouter(prefix="/person", tags=["Person"])


class PersonInfoResponse(BaseModel):
    """人物信息响应"""

    id: int
    is_known: bool
    person_id: str
    individual_id: str
    source_person_name: Optional[str]
    person_name: Optional[str]
    platform: str
    user_id: str
    nickname: Optional[str]
    group_nick_name: Optional[List[Dict[str, str]]]  # 解析后的 JSON
    know_times: Optional[int]
    know_since: Optional[float]
    last_know: Optional[float]


class PersonListResponse(BaseModel):
    """人物列表响应"""

    success: bool
    total: int
    page: int
    page_size: int
    data: List[PersonInfoResponse]


class PersonDetailResponse(BaseModel):
    """人物详情响应"""

    success: bool
    data: PersonInfoResponse


class PersonUpdateRequest(BaseModel):
    """人物信息更新请求"""

    expected_individual_id: Optional[str] = None
    expected_profile: Optional[IndividualProfile] = None
    person_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    nickname: Optional[str] = None
    is_known: Optional[bool] = None


class PersonUpdateResponse(BaseModel):
    """人物信息更新响应"""

    success: bool
    message: str
    data: Optional[PersonInfoResponse] = None


class PersonDeleteResponse(BaseModel):
    """人物删除响应"""

    success: bool
    message: str


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    person_ids: List[str]


class BatchDeleteResponse(BaseModel):
    """批量删除响应"""

    success: bool
    message: str
    deleted_count: int
    failed_count: int
    failed_ids: List[str] = []


def parse_group_nick_name(group_nick_name_str: Optional[str]) -> Optional[List[Dict[str, str]]]:
    """解析群昵称 JSON 字符串。

    Args:
        group_nick_name_str: 数据库中保存的群昵称 JSON 字符串。

    Returns:
        Optional[List[Dict[str, str]]]: 解析后的群昵称列表，解析失败时返回 None。
    """
    if not group_nick_name_str:
        return None
    try:
        return json.loads(group_nick_name_str)
    except (json.JSONDecodeError, TypeError):
        return None


def person_to_response(person: PersonInfo) -> PersonInfoResponse:
    """将人物信息模型转换为响应对象。

    Args:
        person: 数据库中的人物信息记录。

    Returns:
        PersonInfoResponse: WebUI 可直接序列化的人物信息。
    """
    know_since = person.first_known_time.timestamp() if person.first_known_time else None
    last_know = person.last_known_time.timestamp() if person.last_known_time else None
    return PersonInfoResponse(
        id=person.id or 0,
        is_known=person.individual.is_known,
        person_id=person.person_id,
        individual_id=person.individual_id,
        source_person_name=person.person_name,
        person_name=person.individual.display_name,
        platform=person.platform,
        user_id=person.user_id,
        nickname=person.user_nickname,
        group_nick_name=parse_group_nick_name(person.group_cardname),
        know_times=person.know_counts,
        know_since=know_since,
        last_know=last_know,
    )


@router.get("/list", response_model=PersonListResponse)
def get_person_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_known: Optional[bool] = Query(None, description="是否已认识筛选"),
    platform: Optional[str] = Query(None, description="平台筛选"),
    user_id: Optional[str] = Query(None, description="平台用户 ID 精确筛选"),
    exclude_numeric_user_ids: bool = Query(False, description="隐藏纯数字用户 ID"),
) -> PersonListResponse:
    """获取人物信息列表。

    Args:
        page: 页码，从 1 开始。
        page_size: 每页数量，范围为 1-100。
        search: 搜索关键词，用于匹配人物名称、昵称和用户 ID。
        is_known: 是否已认识筛选条件。
        platform: 平台筛选条件。
        user_id: 平台用户 ID 精确筛选条件。
        exclude_numeric_user_ids: 隐藏纯数字用户 ID，不删除用户或关联数据。

    Returns:
        PersonListResponse: 分页后的人物信息列表。
    """
    try:
        # 构建查询
        statement = select(PersonInfo).join(Individual, PersonInfo.individual_id == Individual.individual_id)

        # 搜索过滤
        if search:
            statement = statement.where(
                (col(Individual.display_name).contains(search))
                | (col(PersonInfo.user_nickname).contains(search))
                | (col(PersonInfo.user_id).contains(search))
            )

        # 已认识状态过滤
        if is_known is not None:
            statement = statement.where(col(Individual.is_known) == is_known)

        # 平台过滤
        if platform:
            statement = statement.where(col(PersonInfo.platform) == platform)
        if user_id:
            statement = statement.where(col(PersonInfo.user_id) == user_id)

        if exclude_numeric_user_ids:
            statement = statement.where(col(PersonInfo.user_id).op("GLOB")("*[^0-9]*"))

        # 排序：最后更新时间倒序（NULL 值放在最后）
        # Peewee 不支持 nulls_last，使用 CASE WHEN 来实现
        statement = statement.order_by(
            case((col(PersonInfo.last_known_time).is_(None), 1), else_=0),
            col(PersonInfo.last_known_time).desc(),
        )

        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)

        with get_db_session() as session:
            persons = session.exec(statement).all()

            count_statement = select(PersonInfo.id).join(
                Individual, PersonInfo.individual_id == Individual.individual_id
            )
            if search:
                count_statement = count_statement.where(
                    (col(Individual.display_name).contains(search))
                    | (col(PersonInfo.user_nickname).contains(search))
                    | (col(PersonInfo.user_id).contains(search))
                )
            if is_known is not None:
                count_statement = count_statement.where(col(Individual.is_known) == is_known)
            if platform:
                count_statement = count_statement.where(col(PersonInfo.platform) == platform)
            if user_id:
                count_statement = count_statement.where(col(PersonInfo.user_id) == user_id)
            if exclude_numeric_user_ids:
                count_statement = count_statement.where(col(PersonInfo.user_id).op("GLOB")("*[^0-9]*"))
            total = len(session.exec(count_statement).all())
            data = [person_to_response(person) for person in persons]

        return PersonListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取人物列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}") from e


@router.get("/{person_id}", response_model=PersonDetailResponse)
def get_person_detail(person_id: str) -> PersonDetailResponse:
    """获取人物详细信息。

    Args:
        person_id: 人物唯一 ID。

    Returns:
        PersonDetailResponse: 指定人物的详细信息。
    """
    try:
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()

            if not person:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")

            data = person_to_response(person)

        return PersonDetailResponse(success=True, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取人物详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取人物详情失败: {str(e)}") from e


@router.patch("/{person_id}", response_model=PersonUpdateResponse)
def update_person(
    person_id: str,
    request: PersonUpdateRequest,
) -> PersonUpdateResponse:
    """增量更新人物信息。

    Args:
        person_id: 人物唯一 ID。
        request: 只包含需要更新字段的请求数据。

    Returns:
        PersonUpdateResponse: 更新结果和更新后的人物信息。
    """
    try:
        # 只更新提供的字段
        update_data = request.model_dump(exclude_unset=True, exclude={"expected_individual_id", "expected_profile"})

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # 执行更新
        with get_db_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            db_person = session.exec(select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)).first()
            if not db_person:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")
            individual = db_person.individual
            if {"person_name", "is_known"} & update_data.keys():
                if request.expected_individual_id != individual.individual_id or request.expected_profile != profile_of(
                    individual
                ):
                    raise HTTPException(status_code=409, detail="人物资料或归属已变化，请重新打开编辑窗口")
            if "person_name" in update_data:
                if update_data["person_name"] is None or not update_data["person_name"].strip():
                    raise HTTPException(status_code=400, detail="人物名称不能为空")
                individual.display_name = update_data["person_name"].strip()
            if "nickname" in update_data:
                db_person.user_nickname = update_data["nickname"]
            if "is_known" in update_data:
                if update_data["is_known"] is None:
                    raise HTTPException(status_code=400, detail="认识状态不能为空")
                individual.is_known = update_data["is_known"]
            session.add(individual)
            session.add(db_person)
            data = person_to_response(db_person)

        logger.info(f"人物信息已更新: {person_id}, 字段: {list(update_data.keys())}")

        return PersonUpdateResponse(success=True, message=f"成功更新 {len(update_data)} 个字段", data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"更新人物信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新人物信息失败: {str(e)}") from e


@router.delete("/{person_id}", response_model=PersonDeleteResponse)
def delete_person(person_id: str) -> PersonDeleteResponse:
    """删除人物信息。

    Args:
        person_id: 人物唯一 ID。

    Returns:
        PersonDeleteResponse: 删除结果。
    """
    try:
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()

            if not person:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")

            # 记录删除信息
            person_name = person.person_name or person.user_nickname or person.user_id

            session.exec(delete(PersonInfo).where(col(PersonInfo.person_id) == person_id))

        logger.info(f"人物信息已删除: {person_id} ({person_name})")

        return PersonDeleteResponse(success=True, message=f"成功删除人物信息: {person_name}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除人物信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除人物信息失败: {str(e)}") from e


@router.get("/stats/summary")
def get_person_stats() -> Dict[str, Any]:
    """获取人物信息统计数据。

    Returns:
        Dict[str, Any]: 人物总数、已认识数量和平台分布统计。
    """
    try:
        with get_db_session() as session:
            total = session.exec(select(func.count(PersonInfo.id))).one()
            known = session.exec(
                select(func.count(PersonInfo.id))
                .join(Individual, PersonInfo.individual_id == Individual.individual_id)
                .where(col(Individual.is_known))
            ).one()
            member_counts = (
                select(func.count(PersonInfo.id).label("count")).group_by(PersonInfo.individual_id).subquery()
            )
            individuals = session.exec(select(func.count()).select_from(member_counts)).one()
            linked = session.exec(
                select(func.count()).select_from(member_counts).where(member_counts.c.count > 1)
            ).one()
            platforms = dict(
                session.exec(select(PersonInfo.platform, func.count(PersonInfo.id)).group_by(PersonInfo.platform)).all()
            )
        return {
            "success": True,
            "data": {
                "total": total,
                "known": known,
                "unknown": total - known,
                "individuals": individuals,
                "linked_individuals": linked,
                "platforms": platforms,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
def batch_delete_persons(
    request: BatchDeleteRequest,
) -> BatchDeleteResponse:
    """批量删除人物信息。

    Args:
        request: 包含人物 ID 列表的请求。

    Returns:
        BatchDeleteResponse: 批量删除结果。
    """
    try:
        if not request.person_ids:
            raise HTTPException(status_code=400, detail="未提供要删除的人物ID")

        deleted_count = 0
        failed_count = 0
        failed_ids = []

        for person_id in request.person_ids:
            try:
                with get_db_session() as session:
                    person = session.exec(
                        select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
                    ).first()
                    if person:
                        session.exec(delete(PersonInfo).where(col(PersonInfo.person_id) == person_id))
                        deleted_count += 1
                        logger.info(f"批量删除: {person_id}")
                    else:
                        failed_count += 1
                        failed_ids.append(person_id)
            except Exception as e:
                logger.error(f"删除 {person_id} 失败: {e}")
                failed_count += 1
                failed_ids.append(person_id)

        message = f"成功删除 {deleted_count} 个人物"
        if failed_count > 0:
            message += f"，{failed_count} 个失败"

        return BatchDeleteResponse(
            success=True,
            message=message,
            deleted_count=deleted_count,
            failed_count=failed_count,
            failed_ids=failed_ids,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量删除人物信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除失败: {str(e)}") from e


class IndividualResponse(BaseModel):
    individual_id: str
    display_name: str
    is_known: bool
    profile_sources: List[Dict[str, Any]]
    members: List[PersonInfoResponse]


class IndividualMergeRequest(BaseModel):
    target_individual_id: str
    expected_members: Dict[str, str] = Field(min_length=2, max_length=1000)
    expected_revision: str
    profile: IndividualProfile
    resolved_conflicts: List[ProfileField]


@router.get("/individual/{individual_id}", response_model=IndividualResponse)
def get_individual(individual_id: str) -> IndividualResponse:
    with get_db_session() as session:
        individual = session.get(Individual, individual_id)
        if individual is None:
            raise HTTPException(status_code=404, detail="个体不存在")
        return IndividualResponse(
            individual_id=individual.individual_id,
            display_name=individual.display_name,
            is_known=individual.is_known,
            profile_sources=json.loads(individual.profile_sources_json),
            members=[person_to_response(person) for person in get_individual_members(session, individual_id)],
        )


@router.post("/individual/merge", response_model=IndividualResponse)
def merge_individual(request: IndividualMergeRequest) -> IndividualResponse:
    try:
        with get_db_session() as session:
            # 在检查成员快照前取得写锁，保证预览校验、归属更新和审计记录原子提交。
            session.execute(text("BEGIN IMMEDIATE"))
            individual = merge_individuals(
                session,
                target_individual_id=request.target_individual_id,
                expected_members=request.expected_members,
                expected_revision=request.expected_revision,
                profile=request.profile,
                resolved_conflicts=request.resolved_conflicts,
            )
            return IndividualResponse(
                individual_id=individual.individual_id,
                display_name=individual.display_name,
                is_known=individual.is_known,
                profile_sources=json.loads(individual.profile_sources_json),
                members=[
                    person_to_response(person) for person in get_individual_members(session, individual.individual_id)
                ],
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class IndividualDirectoryEntry(IndividualResponse):
    """个体视图包含完整成员，账号视图包含当前行账号。"""

    member_count: int


class IndividualDirectoryResponse(BaseModel):
    data: List[IndividualDirectoryEntry]
    total: int
    page: int
    page_size: int


@router.get("/directory/list", response_model=IndividualDirectoryResponse)
def get_individual_directory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    view: Literal["individuals", "persons"] = Query("individuals"),
    search: str = Query(""),
    platform: Optional[str] = Query(None),
    is_known: Optional[bool] = Query(None),
    linked_only: bool = Query(False),
) -> IndividualDirectoryResponse:
    """按个体或账号分页。搜索命中任一成员时，个体视图仍展示完整对应关系。"""
    with get_db_session() as session:
        membership = (
            select(
                PersonInfo.individual_id,
                func.count(PersonInfo.id).label("member_count"),
                func.max(PersonInfo.last_known_time).label("last_active"),
            )
            .group_by(PersonInfo.individual_id)
            .subquery()
        )
        matches = select(PersonInfo).join(Individual, PersonInfo.individual_id == Individual.individual_id)
        if search.strip():
            keyword = search.strip()
            matches = matches.where(
                col(Individual.display_name).contains(keyword)
                | col(Individual.individual_id).contains(keyword)
                | col(Individual.display_name).contains(keyword)
                | col(PersonInfo.user_nickname).contains(keyword)
                | col(PersonInfo.user_id).contains(keyword)
                | col(PersonInfo.person_id).contains(keyword)
            )
        if platform:
            matches = matches.where(PersonInfo.platform == platform)
        if is_known is not None:
            matches = matches.where(Individual.is_known == is_known)
        if linked_only:
            matches = matches.where(
                col(PersonInfo.individual_id).in_(
                    select(membership.c.individual_id).where(membership.c.member_count > 1)
                )
            )
        matched = matches.subquery()
        offset = (page - 1) * page_size
        if view == "individuals":
            groups = (
                select(Individual, membership.c.member_count)
                .join(membership, Individual.individual_id == membership.c.individual_id)
                .where(col(Individual.individual_id).in_(select(matched.c.individual_id)))
            )
            total = session.exec(select(func.count()).select_from(groups.subquery())).one()
            rows = session.exec(
                groups.order_by(membership.c.last_active.desc().nulls_last(), Individual.individual_id)
                .offset(offset)
                .limit(page_size)
            ).all()
            ids = [individual.individual_id for individual, _ in rows]
            members_by_id: Dict[str, List[PersonInfoResponse]] = {key: [] for key in ids}
            if ids:
                members = session.exec(
                    select(PersonInfo)
                    .where(col(PersonInfo.individual_id).in_(ids))
                    .order_by(PersonInfo.platform, PersonInfo.id)
                ).all()
                for person in members:
                    members_by_id[person.individual_id].append(person_to_response(person))
            data = [
                IndividualDirectoryEntry(
                    individual_id=individual.individual_id,
                    display_name=individual.display_name,
                    is_known=individual.is_known,
                    profile_sources=json.loads(individual.profile_sources_json),
                    member_count=count,
                    members=members_by_id[individual.individual_id],
                )
                for individual, count in rows
            ]
        else:
            total = session.exec(select(func.count()).select_from(matched)).one()
            people = session.exec(
                matches.order_by(col(PersonInfo.last_known_time).desc().nulls_last(), PersonInfo.id)
                .offset(offset)
                .limit(page_size)
            ).all()
            ids = {person.individual_id for person in people}
            owners = {
                individual.individual_id: (individual, count)
                for individual, count in session.exec(
                    select(Individual, membership.c.member_count)
                    .join(membership, Individual.individual_id == membership.c.individual_id)
                    .where(col(Individual.individual_id).in_(ids))
                ).all()
            }
            data = [
                IndividualDirectoryEntry(
                    individual_id=person.individual_id,
                    display_name=owners[person.individual_id][0].display_name,
                    is_known=owners[person.individual_id][0].is_known,
                    profile_sources=json.loads(owners[person.individual_id][0].profile_sources_json),
                    member_count=owners[person.individual_id][1],
                    members=[person_to_response(person)],
                )
                for person in people
            ]
        return IndividualDirectoryResponse(data=data, total=total, page=page, page_size=page_size)


class IndividualMergePreviewRequest(BaseModel):
    target_individual_id: str
    individual_ids: List[str] = Field(min_length=1, max_length=100)


@router.post("/individual/preview", response_model=IndividualMergePreview)
def preview_individual(request: IndividualMergePreviewRequest) -> IndividualMergePreview:
    try:
        with get_db_session() as session:
            session.execute(text("BEGIN"))
            return preview_individual_merge(
                session, target_individual_id=request.target_individual_id, individual_ids=request.individual_ids
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

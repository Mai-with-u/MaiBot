"""人物信息管理 API 路由"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case
from sqlmodel import col, delete, select

import json

from src.common.database.database import get_db_session
from src.common.database.database_model import PersonInfo
from src.common.logger import get_logger
from src.webui.dependencies import require_auth

logger = get_logger("webui.person")

# 创建路由器
router = APIRouter(prefix="/person", tags=["Person"], dependencies=[Depends(require_auth)])


class PersonInfoResponse(BaseModel):
    """人物信息响应"""

    id: int
    is_known: bool
    person_id: str
    person_name: Optional[str]
    name_reason: Optional[str]
    platform: str
    user_id: str
    nickname: Optional[str]
    group_nick_name: Optional[List[Dict[str, str]]]  # 解析后的 JSON
    memory_points: Optional[str]
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

    person_name: Optional[str] = None
    name_reason: Optional[str] = None
    nickname: Optional[str] = None
    memory_points: Optional[str] = None
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
        is_known=person.is_known,
        person_id=person.person_id,
        person_name=person.person_name,
        name_reason=person.name_reason,
        platform=person.platform,
        user_id=person.user_id,
        nickname=person.user_nickname,
        group_nick_name=parse_group_nick_name(person.group_cardname),
        memory_points=person.memory_points,
        know_times=person.know_counts,
        know_since=know_since,
        last_know=last_know,
    )


@router.get("/list", response_model=PersonListResponse)
async def get_person_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_known: Optional[bool] = Query(None, description="是否已认识筛选"),
    platform: Optional[str] = Query(None, description="平台筛选"),
    user_id: Optional[str] = Query(None, description="平台用户 ID 精确筛选"),
) -> PersonListResponse:
    """获取人物信息列表。

    Args:
        page: 页码，从 1 开始。
        page_size: 每页数量，范围为 1-100。
        search: 搜索关键词，用于匹配人物名称、昵称和用户 ID。
        is_known: 是否已认识筛选条件。
        platform: 平台筛选条件。
        user_id: 平台用户 ID 精确筛选条件。

    Returns:
        PersonListResponse: 分页后的人物信息列表。
    """
    try:
        # 构建查询
        statement = select(PersonInfo)

        # 搜索过滤
        if search:
            statement = statement.where(
                (col(PersonInfo.person_name).contains(search))
                | (col(PersonInfo.user_nickname).contains(search))
                | (col(PersonInfo.user_id).contains(search))
            )

        # 已认识状态过滤
        if is_known is not None:
            statement = statement.where(col(PersonInfo.is_known) == is_known)

        # 平台过滤
        if platform:
            statement = statement.where(col(PersonInfo.platform) == platform)
        if user_id:
            statement = statement.where(col(PersonInfo.user_id) == user_id)

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

            count_statement = select(PersonInfo.id)
            if search:
                count_statement = count_statement.where(
                    (col(PersonInfo.person_name).contains(search))
                    | (col(PersonInfo.user_nickname).contains(search))
                    | (col(PersonInfo.user_id).contains(search))
                )
            if is_known is not None:
                count_statement = count_statement.where(col(PersonInfo.is_known) == is_known)
            if platform:
                count_statement = count_statement.where(col(PersonInfo.platform) == platform)
            if user_id:
                count_statement = count_statement.where(col(PersonInfo.user_id) == user_id)
            total = len(session.exec(count_statement).all())
            data = [person_to_response(person) for person in persons]

        return PersonListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取人物列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}") from e


@router.get("/{person_id}", response_model=PersonDetailResponse)
async def get_person_detail(person_id: str) -> PersonDetailResponse:
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
async def update_person(
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
        update_data = request.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # 更新最后修改时间
        update_data["last_known_time"] = datetime.now()

        # 执行更新
        with get_db_session() as session:
            db_person = session.exec(select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)).first()
            if not db_person:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")
            if "person_name" in update_data:
                db_person.person_name = update_data["person_name"]
            if "name_reason" in update_data:
                db_person.name_reason = update_data["name_reason"]
            if "nickname" in update_data:
                db_person.user_nickname = update_data["nickname"]
            if "memory_points" in update_data:
                db_person.memory_points = update_data["memory_points"]
            if "is_known" in update_data:
                db_person.is_known = update_data["is_known"]
            db_person.last_known_time = update_data["last_known_time"]
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
async def delete_person(person_id: str) -> PersonDeleteResponse:
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
async def get_person_stats() -> Dict[str, Any]:
    """获取人物信息统计数据。

    Returns:
        Dict[str, Any]: 人物总数、已认识数量和平台分布统计。
    """
    try:
        with get_db_session() as session:
            total = len(session.exec(select(PersonInfo.id)).all())
            known = len(session.exec(select(PersonInfo.id).where(col(PersonInfo.is_known))).all())
        unknown = total - known

        # 按平台统计
        platforms = {}
        with get_db_session() as session:
            for platform in session.exec(select(PersonInfo.platform)).all():
                if platform:
                    platforms[platform] = platforms.get(platform, 0) + 1

        return {"success": True, "data": {"total": total, "known": known, "unknown": unknown, "platforms": platforms}}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
async def batch_delete_persons(
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

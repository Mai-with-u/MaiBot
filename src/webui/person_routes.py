"""人物信息管理 API 路由"""

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel
from typing import Optional, List, Dict
from src.common.logger import get_logger
from src.common.database.database_model import PersonInfo
from .token_manager import get_token_manager
import json
import time

logger = get_logger("webui.person")

# 创建路由器
router = APIRouter(prefix="/person", tags=["Person"])


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
    know_times: Optional[float]
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


def verify_auth_token(authorization: Optional[str]) -> bool:
    """验证认证 Token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")

    token = authorization.replace("Bearer ", "")
    token_manager = get_token_manager()

    if not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return True


def parse_group_nick_name(group_nick_name_str: Optional[str]) -> Optional[List[Dict[str, str]]]:
    """解析群昵称 JSON 字符串"""
    if not group_nick_name_str:
        return None
    try:
        return json.loads(group_nick_name_str)
    except (json.JSONDecodeError, TypeError):
        return None


def person_to_response(person: PersonInfo) -> PersonInfoResponse:
    """将 PersonInfo 模型转换为响应对象"""
    return PersonInfoResponse(
        id=person.id,
        is_known=person.is_known,
        person_id=person.person_id,
        person_name=person.person_name,
        name_reason=person.name_reason,
        platform=person.platform,
        user_id=person.user_id,
        nickname=person.nickname,
        group_nick_name=parse_group_nick_name(person.group_nick_name),
        memory_points=person.memory_points,
        know_times=person.know_times,
        know_since=person.know_since,
        last_know=person.last_know,
    )


@router.get("/list", response_model=PersonListResponse)
async def get_person_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_known: Optional[bool] = Query(None, description="是否已认识筛选"),
    platform: Optional[str] = Query(None, description="平台筛选"),
    authorization: Optional[str] = Header(None),
):
    """
    获取人物信息列表

    Args:
        page: 页码 (从 1 开始)
        page_size: 每页数量 (1-100)
        search: 搜索关键词 (匹配 person_name, nickname, user_id)
        is_known: 是否已认识筛选
        platform: 平台筛选
        authorization: Authorization header

    Returns:
        人物信息列表
    """
    try:
        verify_auth_token(authorization)

        # 构建查询
        query = PersonInfo.select()

        # 搜索过滤
        if search:
            query = query.where(
                (PersonInfo.person_name.contains(search))
                | (PersonInfo.nickname.contains(search))
                | (PersonInfo.user_id.contains(search))
            )

        # 已认识状态过滤
        if is_known is not None:
            query = query.where(PersonInfo.is_known == is_known)

        # 平台过滤
        if platform:
            query = query.where(PersonInfo.platform == platform)

        # 排序：最后更新时间倒序（NULL 值放在最后）
        # Peewee 不支持 nulls_last，使用 CASE WHEN 来实现
        from peewee import Case

        query = query.order_by(Case(None, [(PersonInfo.last_know.is_null(), 1)], 0), PersonInfo.last_know.desc())

        # 获取总数
        total = query.count()

        # 分页
        offset = (page - 1) * page_size
        persons = query.offset(offset).limit(page_size)

        # 转换为响应对象
        data = [person_to_response(person) for person in persons]

        return PersonListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取人物列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}") from e


@router.get("/{person_id}", response_model=PersonDetailResponse)
async def get_person_detail(person_id: str, authorization: Optional[str] = Header(None)):
    """
    获取人物详细信息

    Args:
        person_id: 人物唯一 ID
        authorization: Authorization header

    Returns:
        人物详细信息
    """
    try:
        verify_auth_token(authorization)

        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)

        if not person:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")

        return PersonDetailResponse(success=True, data=person_to_response(person))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取人物详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取人物详情失败: {str(e)}") from e


@router.patch("/{person_id}", response_model=PersonUpdateResponse)
async def update_person(person_id: str, request: PersonUpdateRequest, authorization: Optional[str] = Header(None)):
    """
    增量更新人物信息（只更新提供的字段）

    Args:
        person_id: 人物唯一 ID
        request: 更新请求（只包含需要更新的字段）
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(authorization)

        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)

        if not person:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")

        # 只更新提供的字段
        update_data = request.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # 更新最后修改时间
        update_data["last_know"] = time.time()

        # 执行更新
        for field, value in update_data.items():
            setattr(person, field, value)

        person.save()

        logger.info(f"人物信息已更新: {person_id}, 字段: {list(update_data.keys())}")

        return PersonUpdateResponse(
            success=True, message=f"成功更新 {len(update_data)} 个字段", data=person_to_response(person)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"更新人物信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新人物信息失败: {str(e)}") from e


@router.delete("/{person_id}", response_model=PersonDeleteResponse)
async def delete_person(person_id: str, authorization: Optional[str] = Header(None)):
    """
    删除人物信息

    Args:
        person_id: 人物唯一 ID
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(authorization)

        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)

        if not person:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {person_id} 的人物信息")

        # 记录删除信息
        person_name = person.person_name or person.nickname or person.user_id

        # 执行删除
        person.delete_instance()

        logger.info(f"人物信息已删除: {person_id} ({person_name})")

        return PersonDeleteResponse(success=True, message=f"成功删除人物信息: {person_name}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除人物信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除人物信息失败: {str(e)}") from e


@router.get("/stats/summary")
async def get_person_stats(authorization: Optional[str] = Header(None)):
    """
    获取人物信息统计数据

    Args:
        authorization: Authorization header

    Returns:
        统计数据
    """
    try:
        verify_auth_token(authorization)

        total = PersonInfo.select().count()
        known = PersonInfo.select().where(PersonInfo.is_known).count()
        unknown = total - known

        # 按平台统计
        platforms = {}
        for person in PersonInfo.select(PersonInfo.platform):
            platform = person.platform
            platforms[platform] = platforms.get(platform, 0) + 1

        return {"success": True, "data": {"total": total, "known": known, "unknown": unknown, "platforms": platforms}}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
async def batch_delete_persons(request: BatchDeleteRequest, authorization: Optional[str] = Header(None)):
    """
    批量删除人物信息

    Args:
        request: 包含person_ids列表的请求
        authorization: Authorization header

    Returns:
        批量删除结果
    """
    try:
        verify_auth_token(authorization)

        if not request.person_ids:
            raise HTTPException(status_code=400, detail="未提供要删除的人物ID")

        deleted_count = 0
        failed_count = 0
        failed_ids = []

        for person_id in request.person_ids:
            try:
                person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)
                if person:
                    person.delete_instance()
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

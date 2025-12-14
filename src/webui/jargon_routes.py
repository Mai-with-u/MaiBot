"""黑话（俚语）管理路由"""

import json
from typing import Optional, List, Annotated
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from peewee import fn

from src.common.logger import get_logger
from src.common.database.database_model import Jargon, ChatStreams

logger = get_logger("webui.jargon")

router = APIRouter(prefix="/jargon", tags=["Jargon"])


# ==================== 辅助函数 ====================


def parse_chat_id_to_stream_ids(chat_id_str: str) -> List[str]:
    """
    解析 chat_id 字段，提取所有 stream_id
    chat_id 格式: [["stream_id", user_id], ...] 或直接是 stream_id 字符串
    """
    if not chat_id_str:
        return []

    try:
        # 尝试解析为 JSON
        parsed = json.loads(chat_id_str)
        if isinstance(parsed, list):
            # 格式: [["stream_id", user_id], ...]
            stream_ids = []
            for item in parsed:
                if isinstance(item, list) and len(item) >= 1:
                    stream_ids.append(str(item[0]))
            return stream_ids
        else:
            # 其他格式，返回原始字符串
            return [chat_id_str]
    except (json.JSONDecodeError, TypeError):
        # 不是有效的 JSON，可能是直接的 stream_id
        return [chat_id_str]


def get_display_name_for_chat_id(chat_id_str: str) -> str:
    """
    获取 chat_id 的显示名称
    尝试解析 JSON 并查询 ChatStreams 表获取群聊名称
    """
    stream_ids = parse_chat_id_to_stream_ids(chat_id_str)

    if not stream_ids:
        return chat_id_str

    # 查询所有 stream_id 对应的名称
    names = []
    for stream_id in stream_ids:
        chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == stream_id)
        if chat_stream and chat_stream.group_name:
            names.append(chat_stream.group_name)
        else:
            # 如果没找到，显示截断的 stream_id
            names.append(stream_id[:8] + "..." if len(stream_id) > 8 else stream_id)

    return ", ".join(names) if names else chat_id_str


# ==================== 请求/响应模型 ====================


class JargonResponse(BaseModel):
    """黑话信息响应"""

    id: int
    content: str
    raw_content: Optional[str] = None
    meaning: Optional[str] = None
    chat_id: str
    stream_id: Optional[str] = None  # 解析后的 stream_id，用于前端编辑时匹配
    chat_name: Optional[str] = None  # 解析后的聊天名称，用于前端显示
    is_global: bool = False
    count: int = 0
    is_jargon: Optional[bool] = None
    is_complete: bool = False
    inference_with_context: Optional[str] = None
    inference_content_only: Optional[str] = None


class JargonListResponse(BaseModel):
    """黑话列表响应"""

    success: bool = True
    total: int
    page: int
    page_size: int
    data: List[JargonResponse]


class JargonDetailResponse(BaseModel):
    """黑话详情响应"""

    success: bool = True
    data: JargonResponse


class JargonCreateRequest(BaseModel):
    """黑话创建请求"""

    content: str = Field(..., description="黑话内容")
    raw_content: Optional[str] = Field(None, description="原始内容")
    meaning: Optional[str] = Field(None, description="含义")
    chat_id: str = Field(..., description="聊天ID")
    is_global: bool = Field(False, description="是否全局")


class JargonUpdateRequest(BaseModel):
    """黑话更新请求"""

    content: Optional[str] = None
    raw_content: Optional[str] = None
    meaning: Optional[str] = None
    chat_id: Optional[str] = None
    is_global: Optional[bool] = None
    is_jargon: Optional[bool] = None


class JargonCreateResponse(BaseModel):
    """黑话创建响应"""

    success: bool = True
    message: str
    data: JargonResponse


class JargonUpdateResponse(BaseModel):
    """黑话更新响应"""

    success: bool = True
    message: str
    data: Optional[JargonResponse] = None


class JargonDeleteResponse(BaseModel):
    """黑话删除响应"""

    success: bool = True
    message: str
    deleted_count: int = 0


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    ids: List[int] = Field(..., description="要删除的黑话ID列表")


class JargonStatsResponse(BaseModel):
    """黑话统计响应"""

    success: bool = True
    data: dict


class ChatInfoResponse(BaseModel):
    """聊天信息响应"""

    chat_id: str
    chat_name: str
    platform: Optional[str] = None
    is_group: bool = False


class ChatListResponse(BaseModel):
    """聊天列表响应"""

    success: bool = True
    data: List[ChatInfoResponse]


# ==================== 工具函数 ====================


def jargon_to_dict(jargon: Jargon) -> dict:
    """将 Jargon ORM 对象转换为字典"""
    # 解析 chat_id 获取显示名称和 stream_id
    chat_name = get_display_name_for_chat_id(jargon.chat_id) if jargon.chat_id else None
    stream_ids = parse_chat_id_to_stream_ids(jargon.chat_id) if jargon.chat_id else []
    stream_id = stream_ids[0] if stream_ids else None

    return {
        "id": jargon.id,
        "content": jargon.content,
        "raw_content": jargon.raw_content,
        "meaning": jargon.meaning,
        "chat_id": jargon.chat_id,
        "stream_id": stream_id,
        "chat_name": chat_name,
        "is_global": jargon.is_global,
        "count": jargon.count,
        "is_jargon": jargon.is_jargon,
        "is_complete": jargon.is_complete,
        "inference_with_context": jargon.inference_with_context,
        "inference_content_only": jargon.inference_content_only,
    }


# ==================== API 端点 ====================


@router.get("/list", response_model=JargonListResponse)
async def get_jargon_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    chat_id: Optional[str] = Query(None, description="按聊天ID筛选"),
    is_jargon: Optional[bool] = Query(None, description="按是否是黑话筛选"),
    is_global: Optional[bool] = Query(None, description="按是否全局筛选"),
):
    """获取黑话列表"""
    try:
        # 构建查询
        query = Jargon.select()

        # 搜索过滤
        if search:
            query = query.where(
                (Jargon.content.contains(search))
                | (Jargon.meaning.contains(search))
                | (Jargon.raw_content.contains(search))
            )

        # 按聊天ID筛选（使用 contains 匹配，因为 chat_id 是 JSON 格式）
        if chat_id:
            # 从传入的 chat_id 中解析出 stream_id
            stream_ids = parse_chat_id_to_stream_ids(chat_id)
            if stream_ids:
                # 使用第一个 stream_id 进行模糊匹配
                query = query.where(Jargon.chat_id.contains(stream_ids[0]))
            else:
                # 如果无法解析，使用精确匹配
                query = query.where(Jargon.chat_id == chat_id)

        # 按是否是黑话筛选
        if is_jargon is not None:
            query = query.where(Jargon.is_jargon == is_jargon)

        # 按是否全局筛选
        if is_global is not None:
            query = query.where(Jargon.is_global == is_global)

        # 获取总数
        total = query.count()

        # 分页和排序（按使用次数降序）
        query = query.order_by(Jargon.count.desc(), Jargon.id.desc())
        query = query.paginate(page, page_size)

        # 转换为响应格式
        data = [jargon_to_dict(j) for j in query]

        return JargonListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=data,
        )

    except Exception as e:
        logger.error(f"获取黑话列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话列表失败: {str(e)}") from e


@router.get("/chats", response_model=ChatListResponse)
async def get_chat_list():
    """获取所有有黑话记录的聊天列表"""
    try:
        # 获取所有不同的 chat_id
        chat_ids = Jargon.select(Jargon.chat_id).distinct().where(Jargon.chat_id.is_null(False))

        chat_id_list = [j.chat_id for j in chat_ids if j.chat_id]

        # 用于按 stream_id 去重
        seen_stream_ids: set[str] = set()

        for chat_id in chat_id_list:
            stream_ids = parse_chat_id_to_stream_ids(chat_id)
            if stream_ids:
                seen_stream_ids.add(stream_ids[0])

        result = []
        for stream_id in seen_stream_ids:
            # 尝试从 ChatStreams 表获取聊天名称
            chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == stream_id)
            if chat_stream:
                result.append(
                    ChatInfoResponse(
                        chat_id=stream_id,  # 使用 stream_id，方便筛选匹配
                        chat_name=chat_stream.group_name or stream_id,
                        platform=chat_stream.platform,
                        is_group=True,
                    )
                )
            else:
                result.append(
                    ChatInfoResponse(
                        chat_id=stream_id,  # 使用 stream_id
                        chat_name=stream_id[:8] + "..." if len(stream_id) > 8 else stream_id,
                        platform=None,
                        is_group=False,
                    )
                )

        return ChatListResponse(success=True, data=result)

    except Exception as e:
        logger.error(f"获取聊天列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取聊天列表失败: {str(e)}") from e


@router.get("/stats/summary", response_model=JargonStatsResponse)
async def get_jargon_stats():
    """获取黑话统计数据"""
    try:
        # 总数量
        total = Jargon.select().count()

        # 已确认是黑话的数量
        confirmed_jargon = Jargon.select().where(Jargon.is_jargon).count()

        # 已确认不是黑话的数量
        confirmed_not_jargon = Jargon.select().where(~Jargon.is_jargon).count()

        # 未判定的数量
        pending = Jargon.select().where(Jargon.is_jargon.is_null()).count()

        # 全局黑话数量
        global_count = Jargon.select().where(Jargon.is_global).count()

        # 已完成推断的数量
        complete_count = Jargon.select().where(Jargon.is_complete).count()

        # 关联的聊天数量
        chat_count = Jargon.select(Jargon.chat_id).distinct().where(Jargon.chat_id.is_null(False)).count()

        # 按聊天统计 TOP 5
        top_chats = (
            Jargon.select(Jargon.chat_id, fn.COUNT(Jargon.id).alias("count"))
            .group_by(Jargon.chat_id)
            .order_by(fn.COUNT(Jargon.id).desc())
            .limit(5)
        )
        top_chats_dict = {j.chat_id: j.count for j in top_chats if j.chat_id}

        return JargonStatsResponse(
            success=True,
            data={
                "total": total,
                "confirmed_jargon": confirmed_jargon,
                "confirmed_not_jargon": confirmed_not_jargon,
                "pending": pending,
                "global_count": global_count,
                "complete_count": complete_count,
                "chat_count": chat_count,
                "top_chats": top_chats_dict,
            },
        )

    except Exception as e:
        logger.error(f"获取黑话统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话统计失败: {str(e)}") from e


@router.get("/{jargon_id}", response_model=JargonDetailResponse)
async def get_jargon_detail(jargon_id: int):
    """获取黑话详情"""
    try:
        jargon = Jargon.get_or_none(Jargon.id == jargon_id)
        if not jargon:
            raise HTTPException(status_code=404, detail="黑话不存在")

        return JargonDetailResponse(success=True, data=jargon_to_dict(jargon))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取黑话详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取黑话详情失败: {str(e)}") from e


@router.post("/", response_model=JargonCreateResponse)
async def create_jargon(request: JargonCreateRequest):
    """创建黑话"""
    try:
        # 检查是否已存在相同内容的黑话
        existing = Jargon.get_or_none((Jargon.content == request.content) & (Jargon.chat_id == request.chat_id))
        if existing:
            raise HTTPException(status_code=400, detail="该聊天中已存在相同内容的黑话")

        # 创建黑话
        jargon = Jargon.create(
            content=request.content,
            raw_content=request.raw_content,
            meaning=request.meaning,
            chat_id=request.chat_id,
            is_global=request.is_global,
            count=0,
            is_jargon=None,
            is_complete=False,
        )

        logger.info(f"创建黑话成功: id={jargon.id}, content={request.content}")

        return JargonCreateResponse(
            success=True,
            message="创建成功",
            data=jargon_to_dict(jargon),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建黑话失败: {str(e)}") from e


@router.patch("/{jargon_id}", response_model=JargonUpdateResponse)
async def update_jargon(jargon_id: int, request: JargonUpdateRequest):
    """更新黑话（增量更新）"""
    try:
        jargon = Jargon.get_or_none(Jargon.id == jargon_id)
        if not jargon:
            raise HTTPException(status_code=404, detail="黑话不存在")

        # 增量更新字段
        update_data = request.model_dump(exclude_unset=True)
        if update_data:
            for field, value in update_data.items():
                if value is not None or field in ["meaning", "raw_content", "is_jargon"]:
                    setattr(jargon, field, value)
            jargon.save()

        logger.info(f"更新黑话成功: id={jargon_id}")

        return JargonUpdateResponse(
            success=True,
            message="更新成功",
            data=jargon_to_dict(jargon),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新黑话失败: {str(e)}") from e


@router.delete("/{jargon_id}", response_model=JargonDeleteResponse)
async def delete_jargon(jargon_id: int):
    """删除黑话"""
    try:
        jargon = Jargon.get_or_none(Jargon.id == jargon_id)
        if not jargon:
            raise HTTPException(status_code=404, detail="黑话不存在")

        content = jargon.content
        jargon.delete_instance()

        logger.info(f"删除黑话成功: id={jargon_id}, content={content}")

        return JargonDeleteResponse(
            success=True,
            message="删除成功",
            deleted_count=1,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除黑话失败: {str(e)}") from e


@router.post("/batch/delete", response_model=JargonDeleteResponse)
async def batch_delete_jargons(request: BatchDeleteRequest):
    """批量删除黑话"""
    try:
        if not request.ids:
            raise HTTPException(status_code=400, detail="ID列表不能为空")

        deleted_count = Jargon.delete().where(Jargon.id.in_(request.ids)).execute()

        logger.info(f"批量删除黑话成功: 删除了 {deleted_count} 条记录")

        return JargonDeleteResponse(
            success=True,
            message=f"成功删除 {deleted_count} 条黑话",
            deleted_count=deleted_count,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量删除黑话失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除黑话失败: {str(e)}") from e


@router.post("/batch/set-jargon", response_model=JargonUpdateResponse)
async def batch_set_jargon_status(
    ids: Annotated[List[int], Query(description="黑话ID列表")],
    is_jargon: Annotated[bool, Query(description="是否是黑话")],
):
    """批量设置黑话状态"""
    try:
        if not ids:
            raise HTTPException(status_code=400, detail="ID列表不能为空")

        updated_count = Jargon.update(is_jargon=is_jargon).where(Jargon.id.in_(ids)).execute()

        logger.info(f"批量更新黑话状态成功: 更新了 {updated_count} 条记录，is_jargon={is_jargon}")

        return JargonUpdateResponse(
            success=True,
            message=f"成功更新 {updated_count} 条黑话状态",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量更新黑话状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量更新黑话状态失败: {str(e)}") from e

"""表达方式管理 API 路由"""

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel
from typing import Optional, List
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from .token_manager import get_token_manager
import time

logger = get_logger("webui.expression")

# 创建路由器
router = APIRouter(prefix="/expression", tags=["Expression"])


class ExpressionResponse(BaseModel):
    """表达方式响应"""

    id: int
    situation: str
    style: str
    context: Optional[str]
    up_content: Optional[str]
    last_active_time: float
    chat_id: str
    create_date: Optional[float]


class ExpressionListResponse(BaseModel):
    """表达方式列表响应"""

    success: bool
    total: int
    page: int
    page_size: int
    data: List[ExpressionResponse]


class ExpressionDetailResponse(BaseModel):
    """表达方式详情响应"""

    success: bool
    data: ExpressionResponse


class ExpressionCreateRequest(BaseModel):
    """表达方式创建请求"""

    situation: str
    style: str
    context: Optional[str] = None
    up_content: Optional[str] = None
    chat_id: str


class ExpressionUpdateRequest(BaseModel):
    """表达方式更新请求"""

    situation: Optional[str] = None
    style: Optional[str] = None
    context: Optional[str] = None
    up_content: Optional[str] = None
    chat_id: Optional[str] = None


class ExpressionUpdateResponse(BaseModel):
    """表达方式更新响应"""

    success: bool
    message: str
    data: Optional[ExpressionResponse] = None


class ExpressionDeleteResponse(BaseModel):
    """表达方式删除响应"""

    success: bool
    message: str


class ExpressionCreateResponse(BaseModel):
    """表达方式创建响应"""

    success: bool
    message: str
    data: ExpressionResponse


def verify_auth_token(authorization: Optional[str]) -> bool:
    """验证认证 Token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")

    token = authorization.replace("Bearer ", "")
    token_manager = get_token_manager()

    if not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return True


def expression_to_response(expression: Expression) -> ExpressionResponse:
    """将 Expression 模型转换为响应对象"""
    return ExpressionResponse(
        id=expression.id,
        situation=expression.situation,
        style=expression.style,
        context=expression.context,
        up_content=expression.up_content,
        last_active_time=expression.last_active_time,
        chat_id=expression.chat_id,
        create_date=expression.create_date,
    )


@router.get("/list", response_model=ExpressionListResponse)
async def get_expression_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    chat_id: Optional[str] = Query(None, description="聊天ID筛选"),
    authorization: Optional[str] = Header(None),
):
    """
    获取表达方式列表

    Args:
        page: 页码 (从 1 开始)
        page_size: 每页数量 (1-100)
        search: 搜索关键词 (匹配 situation, style, context)
        chat_id: 聊天ID筛选
        authorization: Authorization header

    Returns:
        表达方式列表
    """
    try:
        verify_auth_token(authorization)

        # 构建查询
        query = Expression.select()

        # 搜索过滤
        if search:
            query = query.where(
                (Expression.situation.contains(search))
                | (Expression.style.contains(search))
                | (Expression.context.contains(search))
            )

        # 聊天ID过滤
        if chat_id:
            query = query.where(Expression.chat_id == chat_id)

        # 排序：最后活跃时间倒序（NULL 值放在最后）
        from peewee import Case

        query = query.order_by(
            Case(None, [(Expression.last_active_time.is_null(), 1)], 0), Expression.last_active_time.desc()
        )

        # 获取总数
        total = query.count()

        # 分页
        offset = (page - 1) * page_size
        expressions = query.offset(offset).limit(page_size)

        # 转换为响应对象
        data = [expression_to_response(expr) for expr in expressions]

        return ExpressionListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表达方式列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表达方式列表失败: {str(e)}") from e


@router.get("/{expression_id}", response_model=ExpressionDetailResponse)
async def get_expression_detail(expression_id: int, authorization: Optional[str] = Header(None)):
    """
    获取表达方式详细信息

    Args:
        expression_id: 表达方式ID
        authorization: Authorization header

    Returns:
        表达方式详细信息
    """
    try:
        verify_auth_token(authorization)

        expression = Expression.get_or_none(Expression.id == expression_id)

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        return ExpressionDetailResponse(success=True, data=expression_to_response(expression))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表达方式详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表达方式详情失败: {str(e)}") from e


@router.post("/", response_model=ExpressionCreateResponse)
async def create_expression(request: ExpressionCreateRequest, authorization: Optional[str] = Header(None)):
    """
    创建新的表达方式

    Args:
        request: 创建请求
        authorization: Authorization header

    Returns:
        创建结果
    """
    try:
        verify_auth_token(authorization)

        current_time = time.time()

        # 创建表达方式
        expression = Expression.create(
            situation=request.situation,
            style=request.style,
            context=request.context,
            up_content=request.up_content,
            chat_id=request.chat_id,
            last_active_time=current_time,
            create_date=current_time,
        )

        logger.info(f"表达方式已创建: ID={expression.id}, situation={request.situation}")

        return ExpressionCreateResponse(
            success=True, message="表达方式创建成功", data=expression_to_response(expression)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"创建表达方式失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建表达方式失败: {str(e)}") from e


@router.patch("/{expression_id}", response_model=ExpressionUpdateResponse)
async def update_expression(
    expression_id: int, request: ExpressionUpdateRequest, authorization: Optional[str] = Header(None)
):
    """
    增量更新表达方式（只更新提供的字段）

    Args:
        expression_id: 表达方式ID
        request: 更新请求（只包含需要更新的字段）
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(authorization)

        expression = Expression.get_or_none(Expression.id == expression_id)

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        # 只更新提供的字段
        update_data = request.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # 更新最后活跃时间
        update_data["last_active_time"] = time.time()

        # 执行更新
        for field, value in update_data.items():
            setattr(expression, field, value)

        expression.save()

        logger.info(f"表达方式已更新: ID={expression_id}, 字段: {list(update_data.keys())}")

        return ExpressionUpdateResponse(
            success=True, message=f"成功更新 {len(update_data)} 个字段", data=expression_to_response(expression)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"更新表达方式失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新表达方式失败: {str(e)}") from e


@router.delete("/{expression_id}", response_model=ExpressionDeleteResponse)
async def delete_expression(expression_id: int, authorization: Optional[str] = Header(None)):
    """
    删除表达方式

    Args:
        expression_id: 表达方式ID
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(authorization)

        expression = Expression.get_or_none(Expression.id == expression_id)

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        # 记录删除信息
        situation = expression.situation

        # 执行删除
        expression.delete_instance()

        logger.info(f"表达方式已删除: ID={expression_id}, situation={situation}")

        return ExpressionDeleteResponse(success=True, message=f"成功删除表达方式: {situation}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除表达方式失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除表达方式失败: {str(e)}") from e


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    ids: List[int]


@router.post("/batch/delete", response_model=ExpressionDeleteResponse)
async def batch_delete_expressions(request: BatchDeleteRequest, authorization: Optional[str] = Header(None)):
    """
    批量删除表达方式

    Args:
        request: 包含要删除的ID列表的请求
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(authorization)

        if not request.ids:
            raise HTTPException(status_code=400, detail="未提供要删除的表达方式ID")

        # 查找所有要删除的表达方式
        expressions = Expression.select().where(Expression.id.in_(request.ids))
        found_ids = [expr.id for expr in expressions]

        # 检查是否有未找到的ID
        not_found_ids = set(request.ids) - set(found_ids)
        if not_found_ids:
            logger.warning(f"部分表达方式未找到: {not_found_ids}")

        # 执行批量删除
        deleted_count = Expression.delete().where(Expression.id.in_(found_ids)).execute()

        logger.info(f"批量删除了 {deleted_count} 个表达方式")

        return ExpressionDeleteResponse(success=True, message=f"成功删除 {deleted_count} 个表达方式")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量删除表达方式失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除表达方式失败: {str(e)}") from e


@router.get("/stats/summary")
async def get_expression_stats(authorization: Optional[str] = Header(None)):
    """
    获取表达方式统计数据

    Args:
        authorization: Authorization header

    Returns:
        统计数据
    """
    try:
        verify_auth_token(authorization)

        total = Expression.select().count()

        # 按 chat_id 统计
        chat_stats = {}
        for expr in Expression.select(Expression.chat_id):
            chat_id = expr.chat_id
            chat_stats[chat_id] = chat_stats.get(chat_id, 0) + 1

        # 获取最近创建的记录数（7天内）
        seven_days_ago = time.time() - (7 * 24 * 60 * 60)
        recent = (
            Expression.select()
            .where((Expression.create_date.is_null(False)) & (Expression.create_date >= seven_days_ago))
            .count()
        )

        return {
            "success": True,
            "data": {
                "total": total,
                "recent_7days": recent,
                "chat_count": len(chat_stats),
                "top_chats": dict(sorted(chat_stats.items(), key=lambda x: x[1], reverse=True)[:10]),
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e

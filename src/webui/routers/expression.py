"""表达方式管理 API 路由"""

from fastapi import APIRouter, HTTPException, Header, Query, Cookie
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlmodel import col, select, delete

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import Expression
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.webui.core import verify_auth_token_from_cookie_or_header

logger = get_logger("webui.expression")

# 创建路由器
router = APIRouter(prefix="/expression", tags=["Expression"])


class ExpressionResponse(BaseModel):
    """表达方式响应"""

    id: int
    situation: str
    style: str
    last_active_time: float
    chat_id: str
    create_date: Optional[float]
    checked: bool
    rejected: bool
    modified_by: Optional[str] = None  # 'ai' 或 'user' 或 None


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
    chat_id: str


class ExpressionUpdateRequest(BaseModel):
    """表达方式更新请求"""

    situation: Optional[str] = None
    style: Optional[str] = None
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


def verify_auth_token(
    maibot_session: Optional[str] = None,
    authorization: Optional[str] = None,
) -> bool:
    """验证认证 Token，支持 Cookie 和 Header"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


def expression_to_response(expression: Expression) -> ExpressionResponse:
    """将 Expression 模型转换为响应对象"""
    last_active_time = expression.last_active_time.timestamp() if expression.last_active_time else 0.0
    create_date = expression.create_time.timestamp() if expression.create_time else None
    return ExpressionResponse(
        id=expression.id if expression.id is not None else 0,
        situation=expression.situation,
        style=expression.style,
        last_active_time=last_active_time,
        chat_id=expression.session_id or "",
        create_date=create_date,
        checked=False,
        rejected=False,
        modified_by=None,
    )


def get_chat_name(chat_id: str) -> str:
    """根据 chat_id 获取聊天名称"""
    try:
        session = _chat_manager.get_session_by_session_id(chat_id)
        if not session:
            return chat_id
        name = _chat_manager.get_session_name(chat_id)
        return name or chat_id
    except Exception:
        return chat_id


def get_chat_names_batch(chat_ids: List[str]) -> Dict[str, str]:
    """批量获取聊天名称"""
    result = {cid: cid for cid in chat_ids}  # 默认值为原始ID
    try:
        for chat_id in chat_ids:
            if name := _chat_manager.get_session_name(chat_id):
                result[chat_id] = name
    except Exception as e:
        logger.warning(f"批量获取聊天名称失败: {e}")
    return result


class ChatInfo(BaseModel):
    """聊天信息"""

    chat_id: str
    chat_name: str
    platform: Optional[str] = None
    is_group: bool = False


class ChatListResponse(BaseModel):
    """聊天列表响应"""

    success: bool
    data: List[ChatInfo]


@router.get("/chats", response_model=ChatListResponse)
async def get_chat_list(maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    获取所有聊天列表（用于下拉选择）

    Args:
        authorization: Authorization header

    Returns:
        聊天列表
    """
    try:
        verify_auth_token(maibot_session, authorization)

        chat_list = []
        for session_id, session in _chat_manager.sessions.items():
            chat_name = _chat_manager.get_session_name(session_id) or session_id
            chat_list.append(
                ChatInfo(
                    chat_id=session_id,
                    chat_name=chat_name,
                    platform=session.platform,
                    is_group=session.is_group_session,
                )
            )

        # 按名称排序
        chat_list.sort(key=lambda x: x.chat_name)

        return ChatListResponse(success=True, data=chat_list)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取聊天列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取聊天列表失败: {str(e)}") from e


@router.get("/list", response_model=ExpressionListResponse)
async def get_expression_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    chat_id: Optional[str] = Query(None, description="聊天ID筛选"),
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取表达方式列表

    Args:
        page: 页码 (从 1 开始)
        page_size: 每页数量 (1-100)
        search: 搜索关键词 (匹配 situation, style)
        chat_id: 聊天ID筛选
        authorization: Authorization header

    Returns:
        表达方式列表
    """
    try:
        verify_auth_token(maibot_session, authorization)

        # 构建查询
        statement = select(Expression)

        # 搜索过滤
        if search:
            statement = statement.where(
                (col(Expression.situation).contains(search)) | (col(Expression.style).contains(search))
            )

        # 聊天ID过滤
        if chat_id:
            statement = statement.where(col(Expression.session_id) == chat_id)

        # 排序：最后活跃时间倒序（NULL 值放在最后）
        statement = statement.order_by(
            case((col(Expression.last_active_time).is_(None), 1), else_=0),
            col(Expression.last_active_time).desc(),
        )

        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)

        with get_db_session() as session:
            expressions = session.exec(statement).all()

            count_statement = select(Expression.id)
            if search:
                count_statement = count_statement.where(
                    (col(Expression.situation).contains(search)) | (col(Expression.style).contains(search))
                )
            if chat_id:
                count_statement = count_statement.where(col(Expression.session_id) == chat_id)
            total = len(session.exec(count_statement).all())

        data = [expression_to_response(expr) for expr in expressions]

        return ExpressionListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表达方式列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表达方式列表失败: {str(e)}") from e


@router.get("/{expression_id}", response_model=ExpressionDetailResponse)
async def get_expression_detail(
    expression_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
):
    """
    获取表达方式详细信息

    Args:
        expression_id: 表达方式ID
        authorization: Authorization header

    Returns:
        表达方式详细信息
    """
    try:
        verify_auth_token(maibot_session, authorization)

        with get_db_session() as session:
            statement = select(Expression).where(col(Expression.id) == expression_id).limit(1)
            expression = session.exec(statement).first()

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        return ExpressionDetailResponse(success=True, data=expression_to_response(expression))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表达方式详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表达方式详情失败: {str(e)}") from e


@router.post("/", response_model=ExpressionCreateResponse)
async def create_expression(
    request: ExpressionCreateRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    创建新的表达方式

    Args:
        request: 创建请求
        authorization: Authorization header

    Returns:
        创建结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        current_time = datetime.now()

        # 创建表达方式
        with get_db_session() as session:
            expression = Expression(
                situation=request.situation,
                style=request.style,
                context="",
                up_content="",
                content_list="[]",
                count=0,
                last_active_time=current_time,
                create_time=current_time,
                session_id=request.chat_id,
            )
            session.add(expression)

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
    expression_id: int,
    request: ExpressionUpdateRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
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
        verify_auth_token(maibot_session, authorization)

        with get_db_session() as session:
            statement = select(Expression).where(col(Expression.id) == expression_id).limit(1)
            expression = session.exec(statement).first()

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        # 只更新提供的字段
        update_data = request.model_dump(exclude_unset=True)

        # 映射 API 字段名到数据库字段名
        if "chat_id" in update_data:
            update_data["session_id"] = update_data.pop("chat_id")

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # 更新最后活跃时间
        update_data["last_active_time"] = datetime.now()

        # 执行更新
        with get_db_session() as session:
            db_expression = session.exec(select(Expression).where(col(Expression.id) == expression_id).limit(1)).first()
            if not db_expression:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")
            for field, value in update_data.items():
                if hasattr(db_expression, field):
                    setattr(db_expression, field, value)
            session.add(db_expression)
            expression = db_expression

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
async def delete_expression(
    expression_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
):
    """
    删除表达方式

    Args:
        expression_id: 表达方式ID
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        with get_db_session() as session:
            statement = select(Expression).where(col(Expression.id) == expression_id).limit(1)
            expression = session.exec(statement).first()

        if not expression:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {expression_id} 的表达方式")

        # 记录删除信息
        situation = expression.situation

        # 执行删除
        with get_db_session() as session:
            session.exec(delete(Expression).where(col(Expression.id) == expression_id))

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
async def batch_delete_expressions(
    request: BatchDeleteRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    批量删除表达方式

    Args:
        request: 包含要删除的ID列表的请求
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        if not request.ids:
            raise HTTPException(status_code=400, detail="未提供要删除的表达方式ID")

        # 查找所有要删除的表达方式
        with get_db_session() as session:
            statements = select(Expression.id).where(col(Expression.id).in_(request.ids))
            found_ids = list(session.exec(statements).all())

        # 检查是否有未找到的ID
        if not_found_ids := set(request.ids) - set(found_ids):
            logger.warning(f"部分表达方式未找到: {not_found_ids}")

        # 执行批量删除
        with get_db_session() as session:
            result = session.exec(delete(Expression).where(col(Expression.id).in_(found_ids)))
            deleted_count = result.rowcount or 0

        logger.info(f"批量删除了 {deleted_count} 个表达方式")

        return ExpressionDeleteResponse(success=True, message=f"成功删除 {deleted_count} 个表达方式")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量删除表达方式失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除表达方式失败: {str(e)}") from e


@router.get("/stats/summary")
async def get_expression_stats(
    maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
):
    """
    获取表达方式统计数据

    Args:
        authorization: Authorization header

    Returns:
        统计数据
    """
    try:
        verify_auth_token(maibot_session, authorization)

        with get_db_session() as session:
            total = len(session.exec(select(Expression.id)).all())

            chat_stats = {}
            for chat_id in session.exec(select(Expression.session_id)).all():
                if chat_id:
                    chat_stats[chat_id] = chat_stats.get(chat_id, 0) + 1

            seven_days_ago = datetime.now() - timedelta(days=7)
            recent_statement = (
                select(func.count())
                .select_from(Expression)
                .where(col(Expression.create_time).is_not(None), col(Expression.create_time) >= seven_days_ago)
            )
            recent = session.exec(recent_statement).one()

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


# ============ 审核相关接口 ============


class ReviewStatsResponse(BaseModel):
    """审核统计响应"""

    total: int
    unchecked: int
    passed: int
    rejected: int
    ai_checked: int
    user_checked: int


@router.get("/review/stats", response_model=ReviewStatsResponse)
async def get_review_stats(maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    获取审核统计数据

    Returns:
        审核统计数据
    """
    try:
        verify_auth_token(maibot_session, authorization)

        with get_db_session() as session:
            total = len(session.exec(select(Expression.id)).all())
            unchecked = 0
            passed = 0
            rejected = 0
            ai_checked = 0
            user_checked = 0

        return ReviewStatsResponse(
            total=total,
            unchecked=unchecked,
            passed=passed,
            rejected=rejected,
            ai_checked=ai_checked,
            user_checked=user_checked,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取审核统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取审核统计失败: {str(e)}") from e


class ReviewListResponse(BaseModel):
    """审核列表响应"""

    success: bool
    total: int
    page: int
    page_size: int
    data: List[ExpressionResponse]


@router.get("/review/list", response_model=ReviewListResponse)
async def get_review_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    filter_type: str = Query("unchecked", description="筛选类型: unchecked/passed/rejected/all"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    chat_id: Optional[str] = Query(None, description="聊天ID筛选"),
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取待审核/已审核的表达方式列表

    Args:
        page: 页码
        page_size: 每页数量
        filter_type: 筛选类型 (unchecked/passed/rejected/all)
        search: 搜索关键词
        chat_id: 聊天ID筛选

    Returns:
        表达方式列表
    """
    try:
        verify_auth_token(maibot_session, authorization)

        statement = select(Expression)

        if filter_type in {"unchecked", "passed", "rejected"}:
            statement = statement.where(col(Expression.id) == -1)
        # all 不需要额外过滤

        # 搜索过滤
        if search:
            statement = statement.where(
                (col(Expression.situation).contains(search)) | (col(Expression.style).contains(search))
            )

        # 聊天ID过滤
        if chat_id:
            statement = statement.where(col(Expression.session_id) == chat_id)

        # 排序：创建时间倒序
        statement = statement.order_by(
            case((col(Expression.create_time).is_(None), 1), else_=0),
            col(Expression.create_time).desc(),
        )

        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)

        with get_db_session() as session:
            expressions = session.exec(statement).all()

            count_statement = select(Expression.id)
            if filter_type in {"unchecked", "passed", "rejected"}:
                count_statement = count_statement.where(col(Expression.id) == -1)
            if search:
                count_statement = count_statement.where(
                    (col(Expression.situation).contains(search)) | (col(Expression.style).contains(search))
                )
            if chat_id:
                count_statement = count_statement.where(col(Expression.session_id) == chat_id)
            total = len(session.exec(count_statement).all())

        return ReviewListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=[expression_to_response(expr) for expr in expressions],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取审核列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取审核列表失败: {str(e)}") from e


class BatchReviewItem(BaseModel):
    """批量审核项"""

    id: int
    rejected: bool
    require_unchecked: bool = True  # 默认要求未检查状态


class BatchReviewRequest(BaseModel):
    """批量审核请求"""

    items: List[BatchReviewItem]


class BatchReviewResultItem(BaseModel):
    """批量审核结果项"""

    id: int
    success: bool
    message: str


class BatchReviewResponse(BaseModel):
    """批量审核响应"""

    success: bool
    total: int
    succeeded: int
    failed: int
    results: List[BatchReviewResultItem]


@router.post("/review/batch", response_model=BatchReviewResponse)
async def batch_review_expressions(
    request: BatchReviewRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    批量审核表达方式

    Args:
        request: 批量审核请求

    Returns:
        批量审核结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        if not request.items:
            raise HTTPException(status_code=400, detail="未提供要审核的表达方式")

        results = []
        succeeded = 0
        failed = 0

        for item in request.items:
            try:
                with get_db_session() as session:
                    expression = session.exec(select(Expression).where(col(Expression.id) == item.id).limit(1)).first()

                if not expression:
                    results.append(
                        BatchReviewResultItem(id=item.id, success=False, message=f"未找到 ID 为 {item.id} 的表达方式")
                    )
                    failed += 1
                    continue

                # 冲突检测
                if item.require_unchecked:
                    results.append(
                        BatchReviewResultItem(id=item.id, success=False, message="当前模型不支持审核状态过滤")
                    )
                    failed += 1
                    continue

                # 更新状态
                with get_db_session() as session:
                    db_expression = session.exec(
                        select(Expression).where(col(Expression.id) == item.id).limit(1)
                    ).first()
                    if not db_expression:
                        results.append(
                            BatchReviewResultItem(
                                id=item.id, success=False, message=f"未找到 ID 为 {item.id} 的表达方式"
                            )
                        )
                        failed += 1
                        continue
                    db_expression.last_active_time = datetime.now()
                    session.add(db_expression)

                results.append(
                    BatchReviewResultItem(id=item.id, success=True, message="拒绝" if item.rejected else "通过")
                )
                succeeded += 1

            except Exception as e:
                results.append(BatchReviewResultItem(id=item.id, success=False, message=str(e)))
                failed += 1

        logger.info(f"批量审核完成: 成功 {succeeded}, 失败 {failed}")

        return BatchReviewResponse(
            success=True, total=len(request.items), succeeded=succeeded, failed=failed, results=results
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量审核失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量审核失败: {str(e)}") from e

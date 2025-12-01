"""WebUI API 路由"""

from fastapi import APIRouter, HTTPException, Header, Response, Request, Cookie
from pydantic import BaseModel, Field
from typing import Optional
from src.common.logger import get_logger
from .token_manager import get_token_manager
from .auth import set_auth_cookie, clear_auth_cookie
from .config_routes import router as config_router
from .statistics_routes import router as statistics_router
from .person_routes import router as person_router
from .expression_routes import router as expression_router
from .emoji_routes import router as emoji_router
from .plugin_routes import router as plugin_router
from .plugin_progress_ws import get_progress_router
from .routers.system import router as system_router
from .model_routes import router as model_router

logger = get_logger("webui.api")

# 创建路由器
router = APIRouter(prefix="/api/webui", tags=["WebUI"])

# 注册配置管理路由
router.include_router(config_router)
# 注册统计数据路由
router.include_router(statistics_router)
# 注册人物信息管理路由
router.include_router(person_router)
# 注册表达方式管理路由
router.include_router(expression_router)
# 注册表情包管理路由
router.include_router(emoji_router)
# 注册插件管理路由
router.include_router(plugin_router)
# 注册插件进度 WebSocket 路由
router.include_router(get_progress_router())
# 注册系统控制路由
router.include_router(system_router)
# 注册模型列表获取路由
router.include_router(model_router)


class TokenVerifyRequest(BaseModel):
    """Token 验证请求"""

    token: str = Field(..., description="访问令牌")


class TokenVerifyResponse(BaseModel):
    """Token 验证响应"""

    valid: bool = Field(..., description="Token 是否有效")
    message: str = Field(..., description="验证结果消息")
    is_first_setup: bool = Field(False, description="是否为首次设置")


class TokenUpdateRequest(BaseModel):
    """Token 更新请求"""

    new_token: str = Field(..., description="新的访问令牌", min_length=10)


class TokenUpdateResponse(BaseModel):
    """Token 更新响应"""

    success: bool = Field(..., description="是否更新成功")
    message: str = Field(..., description="更新结果消息")


class TokenRegenerateResponse(BaseModel):
    """Token 重新生成响应"""

    success: bool = Field(..., description="是否生成成功")
    token: str = Field(..., description="新生成的令牌")
    message: str = Field(..., description="生成结果消息")


class FirstSetupStatusResponse(BaseModel):
    """首次配置状态响应"""

    is_first_setup: bool = Field(..., description="是否为首次配置")
    message: str = Field(..., description="状态消息")


class CompleteSetupResponse(BaseModel):
    """完成配置响应"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")


class ResetSetupResponse(BaseModel):
    """重置配置响应"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "MaiBot WebUI"}


@router.post("/auth/verify", response_model=TokenVerifyResponse)
async def verify_token(request: TokenVerifyRequest, response: Response):
    """
    验证访问令牌，验证成功后设置 HttpOnly Cookie

    Args:
        request: 包含 token 的验证请求
        response: FastAPI Response 对象

    Returns:
        验证结果（包含首次配置状态）
    """
    try:
        token_manager = get_token_manager()
        is_valid = token_manager.verify_token(request.token)

        if is_valid:
            # 设置 HttpOnly Cookie
            set_auth_cookie(response, request.token)
            # 同时返回首次配置状态，避免额外请求
            is_first_setup = token_manager.is_first_setup()
            return TokenVerifyResponse(valid=True, message="Token 验证成功", is_first_setup=is_first_setup)
        else:
            return TokenVerifyResponse(valid=False, message="Token 无效或已过期")
    except Exception as e:
        logger.error(f"Token 验证失败: {e}")
        raise HTTPException(status_code=500, detail="Token 验证失败") from e


@router.post("/auth/logout")
async def logout(response: Response):
    """
    登出并清除认证 Cookie
    
    Args:
        response: FastAPI Response 对象
    
    Returns:
        登出结果
    """
    clear_auth_cookie(response)
    return {"success": True, "message": "已成功登出"}


@router.get("/auth/check")
async def check_auth_status(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    检查当前认证状态（用于前端判断是否已登录）
    
    Returns:
        认证状态
    """
    try:
        token = None
        
        # 优先从 Cookie 获取
        if maibot_session:
            token = maibot_session
        # 其次从 Header 获取
        elif authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")
        
        if not token:
            return {"authenticated": False}
        
        token_manager = get_token_manager()
        if token_manager.verify_token(token):
            return {"authenticated": True}
        else:
            return {"authenticated": False}
    except Exception:
        return {"authenticated": False}


@router.post("/auth/update", response_model=TokenUpdateResponse)
async def update_token(
    request: TokenUpdateRequest,
    response: Response,
    req: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    更新访问令牌（需要当前有效的 token）

    Args:
        request: 包含新 token 的更新请求
        response: FastAPI Response 对象
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)

    Returns:
        更新结果
    """
    try:
        # 验证当前 token（优先 Cookie，其次 Header）
        current_token = None
        if maibot_session:
            current_token = maibot_session
        elif authorization and authorization.startswith("Bearer "):
            current_token = authorization.replace("Bearer ", "")
        
        if not current_token:
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")

        token_manager = get_token_manager()

        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="当前 Token 无效")

        # 更新 token
        success, message = token_manager.update_token(request.new_token)
        
        # 如果更新成功，更新 Cookie
        if success:
            set_auth_cookie(response, request.new_token)

        return TokenUpdateResponse(success=success, message=message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token 更新失败: {e}")
        raise HTTPException(status_code=500, detail="Token 更新失败") from e


@router.post("/auth/regenerate", response_model=TokenRegenerateResponse)
async def regenerate_token(
    response: Response,
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    重新生成访问令牌（需要当前有效的 token）

    Args:
        response: FastAPI Response 对象
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)

    Returns:
        新生成的 token
    """
    try:
        # 验证当前 token（优先 Cookie，其次 Header）
        current_token = None
        if maibot_session:
            current_token = maibot_session
        elif authorization and authorization.startswith("Bearer "):
            current_token = authorization.replace("Bearer ", "")

        if not current_token:
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")
            
        token_manager = get_token_manager()

        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="当前 Token 无效")

        # 重新生成 token
        new_token = token_manager.regenerate_token()
        
        # 更新 Cookie
        set_auth_cookie(response, new_token)

        return TokenRegenerateResponse(success=True, token=new_token, message="Token 已重新生成")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token 重新生成失败: {e}")
        raise HTTPException(status_code=500, detail="Token 重新生成失败") from e


@router.get("/setup/status", response_model=FirstSetupStatusResponse)
async def get_setup_status(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取首次配置状态

    Args:
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)

    Returns:
        首次配置状态
    """
    try:
        # 验证 token（优先 Cookie，其次 Header）
        current_token = None
        if maibot_session:
            current_token = maibot_session
        elif authorization and authorization.startswith("Bearer "):
            current_token = authorization.replace("Bearer ", "")
        
        if not current_token:
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")

        token_manager = get_token_manager()

        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="Token 无效")

        # 检查是否为首次配置
        is_first = token_manager.is_first_setup()

        return FirstSetupStatusResponse(is_first_setup=is_first, message="首次配置" if is_first else "已完成配置")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取配置状态失败: {e}")
        raise HTTPException(status_code=500, detail="获取配置状态失败") from e


@router.post("/setup/complete", response_model=CompleteSetupResponse)
async def complete_setup(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    标记首次配置完成

    Args:
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)

    Returns:
        完成结果
    """
    try:
        # 验证 token（优先 Cookie，其次 Header）
        current_token = None
        if maibot_session:
            current_token = maibot_session
        elif authorization and authorization.startswith("Bearer "):
            current_token = authorization.replace("Bearer ", "")
        
        if not current_token:
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")

        token_manager = get_token_manager()

        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="Token 无效")

        # 标记配置完成
        success = token_manager.mark_setup_completed()

        return CompleteSetupResponse(success=success, message="配置已完成" if success else "标记失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"标记配置完成失败: {e}")
        raise HTTPException(status_code=500, detail="标记配置完成失败") from e


@router.post("/setup/reset", response_model=ResetSetupResponse)
async def reset_setup(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    重置首次配置状态，允许重新进入配置向导

    Args:
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)

    Returns:
        重置结果
    """
    try:
        # 验证 token（优先 Cookie，其次 Header）
        current_token = None
        if maibot_session:
            current_token = maibot_session
        elif authorization and authorization.startswith("Bearer "):
            current_token = authorization.replace("Bearer ", "")
        
        if not current_token:
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")

        token_manager = get_token_manager()

        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="Token 无效")

        # 重置配置状态
        success = token_manager.reset_setup_status()

        return ResetSetupResponse(success=success, message="配置状态已重置" if success else "重置失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置配置状态失败: {e}")
        raise HTTPException(status_code=500, detail="重置配置状态失败") from e

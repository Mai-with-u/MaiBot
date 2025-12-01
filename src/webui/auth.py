"""
WebUI 认证模块
提供统一的认证依赖，支持 Cookie 和 Header 两种方式
"""

from typing import Optional
from fastapi import HTTPException, Cookie, Header, Response, Request
from src.common.logger import get_logger
from .token_manager import get_token_manager

logger = get_logger("webui.auth")

# Cookie 配置
COOKIE_NAME = "maibot_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7天


def get_current_token(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """
    获取当前请求的 token，优先从 Cookie 获取，其次从 Header 获取
    
    Args:
        request: FastAPI Request 对象
        maibot_session: Cookie 中的 token
        authorization: Authorization Header (Bearer token)
    
    Returns:
        验证通过的 token
    
    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    token = None
    
    # 优先从 Cookie 获取
    if maibot_session:
        token = maibot_session
    # 其次从 Header 获取（兼容旧版本）
    elif authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
    
    if not token:
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")
    
    # 验证 token
    token_manager = get_token_manager()
    if not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    
    return token


def set_auth_cookie(response: Response, token: str) -> None:
    """
    设置认证 Cookie
    
    Args:
        response: FastAPI Response 对象
        token: 要设置的 token
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,  # 防止 JS 读取
        samesite="lax",  # 允许同站导航时发送 Cookie（兼容开发环境代理）
        secure=False,  # 本地开发不强制 HTTPS，生产环境建议设为 True
        path="/",  # 确保 Cookie 在所有路径下可用
    )
    logger.debug(f"已设置认证 Cookie: {token[:8]}...")


def clear_auth_cookie(response: Response) -> None:
    """
    清除认证 Cookie
    
    Args:
        response: FastAPI Response 对象
    """
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.debug("已清除认证 Cookie")


def verify_auth_token_from_cookie_or_header(
    maibot_session: Optional[str] = None,
    authorization: Optional[str] = None,
) -> bool:
    """
    验证认证 Token，支持从 Cookie 或 Header 获取
    
    Args:
        maibot_session: Cookie 中的 token
        authorization: Authorization header (Bearer token)
    
    Returns:
        验证成功返回 True
    
    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    token = None
    
    # 优先从 Cookie 获取
    if maibot_session:
        token = maibot_session
    # 其次从 Header 获取（兼容旧版本）
    elif authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
    
    if not token:
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")
    
    # 验证 token
    token_manager = get_token_manager()
    if not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    
    return True

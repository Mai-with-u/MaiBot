"""
WebUI 认证模块
提供统一的认证依赖，支持 Cookie 和 Header 两种方式
"""
import hashlib
import hmac

import os
from typing import Optional
from fastapi import HTTPException, Cookie, Header, Response, Request
from src.common.logger import get_logger
from .token_manager import get_token_manager
import secrets

logger = get_logger("webui.auth")

# Cookie 配置
COOKIE_NAME = "maibot_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7天

COOKIE_NAME_NEW = "maibot_session_signDx"  # 新版 Cookie 名称
COOKIE_SIGN_NRS = "maibot_session_signCx"  # 签名 Cookie 名称


def _is_secure_environment() -> bool:
    """
    检测是否应该启用安全 Cookie（HTTPS）
    
    Returns:
        bool: 如果应该使用 secure cookie 则返回 True
    """
    # 检查环境变量
    if os.environ.get("WEBUI_SECURE_COOKIE", "").lower() in ("true", "1", "yes"):
        return True
    if os.environ.get("WEBUI_SECURE_COOKIE", "").lower() in ("false", "0", "no"):
        return False
    
    # 检查是否是生产环境
    env = os.environ.get("WEBUI_MODE", "").lower()
    if env in ("production", "prod"):
        return True
    
    # 默认：开发环境不启用（因为通常是 HTTP）
    return False


def get_current_token(
        request: Request,
) -> tuple[str, str | None]:
    """
    获取当前请求的 token，优先从 Cookie 获取，其次从 Header 获取

    Args:
        request: FastAPI Request 对象

    Returns:
        验证通过的 token

    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    sign = None
    authorization = request.headers.get("Authorization")
    token = request.cookies.get(COOKIE_NAME_NEW)
    if not token:
        token = request.cookies.get(COOKIE_NAME)

    # 优先从 Cookie 获取
    if token:
        sign = request.cookies.get(COOKIE_SIGN_NRS)
    # 其次从 Header 获取（兼容旧版本）
    elif authorization and request.headers.get("Authorization").startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    if not token:
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")

    # 验证 token
    token_manager = get_token_manager()
    if not token_manager.verify_token(token, sign):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return token


def set_auth_cookie(response: Response, token: str) -> None:
    """
    设置认证 Cookie

    Args:
        response: FastAPI Response 对象
        token: 要设置的 token
    """
    tk = secrets.token_hex(64)
    sign = hmac.new(
        token.encode('utf-8'),
        tk.encode('utf-8'),
        hashlib.sha256
    )
    # 根据环境决定安全设置
    is_secure = _is_secure_environment()
    
    response.set_cookie(
        key=COOKIE_NAME_NEW,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,  # 防止 JS 读取，阻止 XSS 窃取
        samesite="strict" if is_secure else "lax",  # 生产环境使用 strict 防止 CSRF
        secure=is_secure,  # 生产环境强制 HTTPS
        path="/",  # 确保 Cookie 在所有路径下可用
    )
    response.set_cookie(
        key=COOKIE_SIGN_NRS,
        value=sign.hexdigest(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,  # 防止 JS 读取
        samesite="lax",  # 允许同站导航时发送 Cookie（兼容开发环境代理）
        secure=False,  # 本地开发不强制 HTTPS，生产环境建议设为 True
        path="/",  # 确保 Cookie 在所有路径下可用
    )
    logger.debug(f"已设置认证 Cookie: {token[:8]}... (secure={is_secure})")


def clear_auth_cookie(response: Response) -> None:
    """
    清除认证 Cookie

    Args:
        response: FastAPI Response 对象
    """
    # 保持与 set_auth_cookie 相同的安全设置
    is_secure = _is_secure_environment()
    
    response.delete_cookie(
        key=COOKIE_NAME_NEW,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        key=COOKIE_SIGN_NRS,
        httponly=True,
        samesite="strict" if is_secure else "lax",
        secure=is_secure,
        path="/",
    )
    logger.debug("已清除认证 Cookie")


def verify_auth_token_from_cookie_or_header(
        maibot_session: Optional[str] = None,
        seesion_sign: Optional[str] = None,
        authorization: Optional[str] = None,
) -> bool:
    """
    验证认证 Token，支持从 Cookie 或 Header 获取

    Args:
        maibot_session: Cookie 中的 token
        seesion_sign: Cookie 中的签名 token
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
    if not token_manager.verify_token(token, seesion_sign):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return True

"""
WebUI 认证模块
提供统一的认证依赖，支持 Cookie 和 Header 两种方式
"""

from typing import Optional
from fastapi import HTTPException, Cookie, Header, Response, Request
from src.common.logger import get_logger
from src.config.config import global_config
from .security import get_token_manager

logger = get_logger("webui.auth")

# Cookie 配置
COOKIE_NAME = "maibot_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7天


def _is_secure_environment() -> bool:
    """
    检测是否应该启用安全 Cookie（HTTPS）

    Returns:
        bool: 如果应该使用 secure cookie 则返回 True
    """
    # 从配置读取
    if global_config.webui.secure_cookie:
        logger.info("配置中启用了 secure_cookie")
        return True

    # 检查是否是生产环境
    if global_config.webui.mode == "production":
        logger.info("WebUI运行在生产模式，启用 secure cookie")
        return True

    # 默认：开发环境不启用（因为通常是 HTTP）
    logger.debug("WebUI运行在开发模式，禁用 secure cookie")
    return False


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


def set_auth_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    """
    设置认证 Cookie

    Args:
        response: FastAPI Response 对象
        token: 要设置的 token
        request: FastAPI Request 对象（可选，用于检测协议）
    """
    # 根据环境和实际请求协议决定安全设置
    is_secure = _is_secure_environment()

    # 如果提供了 request，检测实际使用的协议
    if request:
        # 检查 X-Forwarded-Proto header（代理/负载均衡器）
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        if forwarded_proto:
            is_https = forwarded_proto == "https"
            logger.debug(f"检测到 X-Forwarded-Proto: {forwarded_proto}, is_https={is_https}")
        else:
            # 检查 request.url.scheme
            is_https = request.url.scheme == "https"
            logger.debug(f"检测到 scheme: {request.url.scheme}, is_https={is_https}")

        # 如果是 HTTP 连接，强制禁用 secure 标志
        if not is_https and is_secure:
            logger.warning("=" * 80)
            logger.warning("检测到 HTTP 连接但环境配置要求 HTTPS (secure cookie)")
            logger.warning("已自动禁用 secure 标志以允许登录，但建议修改配置：")
            logger.warning("1. 在配置文件中设置: webui.secure_cookie = false")
            logger.warning("2. 如果使用反向代理，请确保正确配置 X-Forwarded-Proto 头")
            logger.warning("=" * 80)
            is_secure = False

    # 设置 Cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,  # 防止 JS 读取，阻止 XSS 窃取
        samesite="lax",  # 使用 lax 以兼容更多场景（开发和生产）
        secure=is_secure,  # 根据实际协议决定
        path="/",  # 确保 Cookie 在所有路径下可用
    )

    logger.info(
        f"已设置认证 Cookie: {token[:8]}... (secure={is_secure}, samesite=lax, httponly=True, path=/, max_age={COOKIE_MAX_AGE})"
    )
    logger.debug(f"完整 token 前缀: {token[:20]}...")


def clear_auth_cookie(response: Response) -> None:
    """
    清除认证 Cookie

    Args:
        response: FastAPI Response 对象
    """
    # 保持与 set_auth_cookie 相同的安全设置
    is_secure = _is_secure_environment()

    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="strict" if is_secure else "lax",
        secure=is_secure,
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

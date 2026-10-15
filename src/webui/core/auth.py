"""WebUI 认证模块。"""

from typing import Optional

from fastapi import Cookie, HTTPException, Request, Response

from src.common.logger import get_logger
from src.config.config import global_config

from .security import get_token_manager

logger = get_logger("webui.auth")

# Cookie 配置
COOKIE_NAME = "maibot_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7天


def _is_secure_environment() -> bool:
    """
    根据 secure_cookie 配置决定是否启用安全 Cookie（HTTPS）。

    Returns:
        bool: 如果应该使用 secure cookie 则返回 True
    """
    # 从配置读取
    if global_config.webui.secure_cookie:
        logger.info("配置中启用了 secure_cookie")
        return True

    # 运行模式不代表访问协议，关闭配置时允许通过 HTTP 使用登录 Cookie。
    logger.debug("配置中未启用 secure_cookie")
    return False


def get_current_token(
    maibot_session: Optional[str] = Cookie(None),
) -> str:
    """
    获取当前请求的 token，仅从 HttpOnly Cookie 获取。

    Args:
        maibot_session: Cookie 中的 token

    Returns:
        验证通过的 token

    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    if not is_token_valid(maibot_session):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    assert maibot_session is not None
    return maibot_session


def is_token_valid(maibot_session: Optional[str]) -> bool:
    """判断认证 token 是否存在且有效。"""
    if not maibot_session:
        return False

    token_manager = get_token_manager()
    return token_manager.verify_token(maibot_session)


def set_auth_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    """
    设置认证 Cookie

    Args:
        response: FastAPI Response 对象
        token: 要设置的 token
        request: FastAPI Request 对象（可选，用于检测协议）
    """
    # 配置决定安全设置；实际请求协议只用于提示部署问题，不得降低配置要求。
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

        # HTTP 连接无法正常使用安全 Cookie，保留 Secure 标志并提示修正部署。
        if not is_https and is_secure:
            logger.warning(
                "检测到 HTTP 连接但配置要求安全 Cookie，已保留 Secure 标志；"
                "请使用 HTTPS，并在反向代理中正确配置 X-Forwarded-Proto"
            )

    # 设置 Cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,  # 防止 JS 读取，阻止 XSS 窃取
        samesite="lax",  # 使用 lax 以兼容更多场景（开发和生产）
        secure=is_secure,  # 遵循安全配置
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
) -> bool:
    """
    验证认证 Cookie。

    Args:
        maibot_session: Cookie 中的 token

    Returns:
        验证成功返回 True

    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    if not is_token_valid(maibot_session):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return True

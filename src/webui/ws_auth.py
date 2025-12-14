"""WebSocket 认证模块

提供所有 WebSocket 端点统一使用的临时 token 认证机制。
临时 token 有效期 60 秒，且只能使用一次，用于解决 WebSocket 握手时 Cookie 不可用的问题。
"""

from fastapi import APIRouter, Cookie, Header, HTTPException
from typing import Optional
import secrets
import time
from src.common.logger import get_logger
from src.webui.token_manager import get_token_manager

logger = get_logger("webui.ws_auth")
router = APIRouter()

# WebSocket 临时 token 存储 {token: (expire_time, session_token)}
# 临时 token 有效期 60 秒，仅用于 WebSocket 握手
_ws_temp_tokens: dict[str, tuple[float, str]] = {}
_WS_TOKEN_EXPIRE_SECONDS = 60


def _cleanup_expired_ws_tokens():
    """清理过期的临时 token"""
    now = time.time()
    expired = [t for t, (exp, _) in _ws_temp_tokens.items() if now > exp]
    for t in expired:
        del _ws_temp_tokens[t]


def generate_ws_token(session_token: str) -> str:
    """生成 WebSocket 临时 token
    
    Args:
        session_token: 原始的 session token
        
    Returns:
        临时 token 字符串
    """
    _cleanup_expired_ws_tokens()
    temp_token = secrets.token_urlsafe(32)
    _ws_temp_tokens[temp_token] = (time.time() + _WS_TOKEN_EXPIRE_SECONDS, session_token)
    logger.debug(f"生成 WS 临时 token: {temp_token[:8]}... 有效期 {_WS_TOKEN_EXPIRE_SECONDS}s")
    return temp_token


def verify_ws_token(temp_token: str) -> bool:
    """验证并消费 WebSocket 临时 token（一次性使用）
    
    Args:
        temp_token: 临时 token
        
    Returns:
        验证是否通过
    """
    _cleanup_expired_ws_tokens()
    if temp_token not in _ws_temp_tokens:
        logger.warning(f"WS token 不存在: {temp_token[:8]}...")
        return False
    expire_time, session_token = _ws_temp_tokens[temp_token]
    if time.time() > expire_time:
        del _ws_temp_tokens[temp_token]
        logger.warning(f"WS token 已过期: {temp_token[:8]}...")
        return False
    # 验证原始 session token 仍然有效
    token_manager = get_token_manager()
    if not token_manager.verify_token(session_token):
        del _ws_temp_tokens[temp_token]
        logger.warning(f"WS token 关联的 session 已失效: {temp_token[:8]}...")
        return False
    # 消费 token（一次性使用）
    del _ws_temp_tokens[temp_token]
    logger.debug(f"WS token 验证成功: {temp_token[:8]}...")
    return True


@router.get("/ws-token")
async def get_ws_token(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取 WebSocket 连接用的临时 token
    
    此端点验证当前会话的 Cookie 或 Authorization header，
    然后返回一个临时 token 用于 WebSocket 握手认证。
    临时 token 有效期 60 秒，且只能使用一次。
    """
    # 获取当前 session token
    session_token = None
    if maibot_session:
        session_token = maibot_session
    elif authorization and authorization.startswith("Bearer "):
        session_token = authorization.replace("Bearer ", "")
    
    if not session_token:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    
    # 验证 session token
    token_manager = get_token_manager()
    if not token_manager.verify_token(session_token):
        raise HTTPException(status_code=401, detail="认证已过期，请重新登录")
    
    # 生成临时 WebSocket token
    ws_token = generate_ws_token(session_token)
    
    return {"success": True, "token": ws_token, "expires_in": _WS_TOKEN_EXPIRE_SECONDS}

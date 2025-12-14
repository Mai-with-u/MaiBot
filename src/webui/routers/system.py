"""
系统控制路由

提供系统重启、状态查询等功能
"""

import os
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Cookie, Header
from pydantic import BaseModel
from src.config.config import MMC_VERSION
from src.common.logger import get_logger
from src.webui.auth import verify_auth_token_from_cookie_or_header

router = APIRouter(prefix="/system", tags=["system"])
logger = get_logger("webui_system")

# 记录启动时间
_start_time = time.time()


def require_auth(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """认证依赖：验证用户是否已登录"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


class RestartResponse(BaseModel):
    """重启响应"""

    success: bool
    message: str


class StatusResponse(BaseModel):
    """状态响应"""

    running: bool
    uptime: float
    version: str
    start_time: str


@router.post("/restart", response_model=RestartResponse)
async def restart_maibot(_auth: bool = Depends(require_auth)):
    """
    重启麦麦主程序

    请求重启当前进程，配置更改将在重启后生效。
    注意：此操作会使麦麦暂时离线。
    """
    import asyncio

    try:
        # 记录重启操作
        logger.info("WebUI 触发重启操作")

        # 定义延迟重启的异步任务
        async def delayed_restart():
            await asyncio.sleep(0.5)  # 延迟0.5秒，确保响应已发送
            # 使用 os._exit(42) 退出当前进程，配合外部 runner 脚本进行重启
            # 42 是约定的重启状态码
            logger.info("WebUI 请求重启，退出代码 42")
            os._exit(42)

        # 创建后台任务执行重启
        asyncio.create_task(delayed_restart())

        # 立即返回成功响应
        return RestartResponse(success=True, message="麦麦正在重启中...")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重启失败: {str(e)}") from e


@router.get("/status", response_model=StatusResponse)
async def get_maibot_status(_auth: bool = Depends(require_auth)):
    """
    获取麦麦运行状态

    返回麦麦的运行状态、运行时长和版本信息。
    """
    try:
        uptime = time.time() - _start_time

        # 尝试获取版本信息（需要根据实际情况调整）
        version = MMC_VERSION  # 可以从配置或常量中读取

        return StatusResponse(
            running=True, uptime=uptime, version=version, start_time=datetime.fromtimestamp(_start_time).isoformat()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}") from e


# 可选：添加更多系统控制功能


@router.post("/reload-config")
async def reload_config(_auth: bool = Depends(require_auth)):
    """
    热重载配置（不重启进程）

    仅重新加载配置文件，某些配置可能需要重启才能生效。
    此功能需要在主程序中实现配置热重载逻辑。
    """
    # 这里需要调用主程序的配置重载函数
    # 示例：await app_instance.reload_config()

    return {"success": True, "message": "配置重载功能待实现"}

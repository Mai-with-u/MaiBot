"""仅宿主 IPC 可访问的本地操作员入口；不属于浏览器可代理的 api/ 路径。"""

from typing import TYPE_CHECKING, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import asyncio

from src.common.runtime_loop import get_main_loop, run_on_main_loop
from src.config.config import global_config

if TYPE_CHECKING:
    from src.cli.bot_console import BotConsole

router = APIRouter(prefix="/_host/console")
_console: Optional["BotConsole"] = None
_lock: Optional[asyncio.Lock] = None


class ConsoleInput(BaseModel):
    text: str = Field(min_length=1, max_length=65536)


def _require_enabled() -> None:
    if not global_config.debug.enable_console_input:
        raise HTTPException(403, "此 Bot 未开启 debug.enable_console_input")
    loop = get_main_loop()
    if loop is None or not loop.is_running():
        raise HTTPException(503, "Bot 主循环尚未就绪")


async def _submit(text: str) -> None:
    global _console, _lock
    from src.cli.bot_console import BotConsole

    # 此锁和驱动仅在 Bot 主循环创建、使用，不能在 WebUI 线程初始化。
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _console is None:
            instance = BotConsole()
            await instance.start()
            _console = instance
        await _console.submit(text)


@router.post("")
async def submit(request: ConsoleInput) -> dict:
    _require_enabled()
    text = request.text.strip()
    if not text:
        raise HTTPException(422, "终端输入不能为空")
    await run_on_main_loop(_submit(text))
    return {"accepted": True}


@router.get("/completions")
async def completions() -> List[str]:
    _require_enabled()

    async def read() -> List[str]:
        from src.cli.bot_console import BotConsole

        return BotConsole.completions()

    return await run_on_main_loop(read())


async def close_console() -> None:
    """由 Bot 主循环在关闭消息系统前释放输出路由。"""
    global _console
    if _console is not None:
        await _console.close()
        _console = None

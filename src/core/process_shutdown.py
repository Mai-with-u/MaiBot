"""进程停机步骤编排。

三个入口共用命名步骤，但必须保持各自原有步骤集合、顺序和错误处理：
- full: ``src/main.py`` 的 ``main()`` finally，无逐步超时
- stepwise_timeout: 根目录 ``bot.py`` 的 ``graceful_shutdown``，逐步超时
- restart_partial: WebUI 重启前清理，逐步 try/except
"""

from collections.abc import Awaitable
from enum import Enum
from typing import Any, Literal, Protocol

import asyncio

from src.common.i18n import t, tn
from src.common.logger import get_logger

ShutdownMode = Literal["full", "stepwise_timeout", "restart_partial"]

_main_logger = get_logger("main")
_webui_logger = get_logger("webui_system")


class SupportsAsyncShutdown(Protocol):
    """可异步关闭的服务，供 WebUI 停机步骤使用。"""

    async def shutdown(self) -> object: ...


class ShutdownStep(str, Enum):
    """进程停机命名步骤。"""

    REQUEST_SHUTDOWN = "request_shutdown"
    WEBUI = "webui"
    EMOJI = "emoji"
    MEMORY_AUTOMATION = "memory_automation"
    MEMORIX = "memorix"
    BRIDGE_ON_STOP = "bridge_on_stop"
    EMIT_ON_STOP = "emit_on_stop"
    RUNTIME_STOP = "runtime_stop"
    TASKS = "tasks"
    MCP = "mcp"
    FILE_WATCHER = "file_watcher"
    CLEAR_MAIN_LOOP = "clear_main_loop"
    CANCEL_REMAINING_TASKS = "cancel_remaining_tasks"


# 三种 mode 的步骤集合与顺序必须与现有入口保持一致，不能互相补齐。
MODE_STEPS: dict[ShutdownMode, tuple[ShutdownStep, ...]] = {
    "full": (
        ShutdownStep.WEBUI,
        ShutdownStep.EMOJI,
        ShutdownStep.MEMORY_AUTOMATION,
        ShutdownStep.MEMORIX,
        ShutdownStep.BRIDGE_ON_STOP,
        ShutdownStep.RUNTIME_STOP,
        ShutdownStep.TASKS,
        ShutdownStep.MCP,
        ShutdownStep.FILE_WATCHER,
        ShutdownStep.CLEAR_MAIN_LOOP,
    ),
    "stepwise_timeout": (
        ShutdownStep.REQUEST_SHUTDOWN,
        ShutdownStep.WEBUI,
        ShutdownStep.EMIT_ON_STOP,
        ShutdownStep.RUNTIME_STOP,
        ShutdownStep.TASKS,
        ShutdownStep.CANCEL_REMAINING_TASKS,
    ),
    "restart_partial": (
        ShutdownStep.EMIT_ON_STOP,
        ShutdownStep.RUNTIME_STOP,
        ShutdownStep.TASKS,
    ),
}

_STEPWISE_TIMEOUTS: dict[ShutdownStep, tuple[float, str]] = {
    ShutdownStep.EMIT_ON_STOP: (5.0, "触发 ON_STOP 事件"),
    ShutdownStep.RUNTIME_STOP: (8.0, "停止插件运行时"),
    ShutdownStep.TASKS: (5.0, "停止异步任务管理器任务"),
}

_RESTART_PARTIAL_ERRORS: dict[ShutdownStep, tuple[Literal["warning", "error"], str, bool]] = {
    ShutdownStep.EMIT_ON_STOP: ("warning", "WebUI 重启前触发 ON_STOP 事件失败: {exc}", False),
    ShutdownStep.RUNTIME_STOP: ("error", "WebUI 重启前停止插件运行时失败: {exc}", True),
    ShutdownStep.TASKS: ("warning", "WebUI 重启前停止异步任务失败: {exc}", False),
}


async def run_process_shutdown(
    mode: ShutdownMode,
    *,
    webui_server: SupportsAsyncShutdown | None = None,
) -> None:
    """按指定模式执行进程停机步骤。"""

    match mode:
        case "full":
            await _run_full(webui_server)
        case "stepwise_timeout":
            await _run_stepwise_timeout(webui_server)
        case "restart_partial":
            await _run_restart_partial()
        case _:
            raise ValueError(f"未知停机模式: {mode}")


async def _run_full(webui_server: SupportsAsyncShutdown | None) -> None:
    """复现 ``main()`` finally：无逐步超时，异常直接抛出。"""

    for step in MODE_STEPS["full"]:
        await _invoke_step(step, webui_server=webui_server, webui_require_truthy=True)


async def _run_stepwise_timeout(webui_server: SupportsAsyncShutdown | None) -> None:
    """复现 ``graceful_shutdown``：request_shutdown 后逐步超时，缺 memorix/MCP/emoji/watcher。"""

    for step in MODE_STEPS["stepwise_timeout"]:
        if step is ShutdownStep.REQUEST_SHUTDOWN:
            await _invoke_step(step, webui_server=webui_server, webui_require_truthy=False)
            _main_logger.info(t("startup.shutdown_started"))
            continue
        if step is ShutdownStep.WEBUI:
            try:
                await _invoke_step(step, webui_server=webui_server, webui_require_truthy=False)
            except Exception as e:
                _main_logger.warning(f"关闭 WebUI 服务器时出错: {e}")
            continue
        if step is ShutdownStep.CANCEL_REMAINING_TASKS:
            await _cancel_remaining_tasks()
            continue

        timeout, step_name = _STEPWISE_TIMEOUTS[step]
        await _await_shutdown_step(
            _invoke_step(step, webui_server=webui_server, webui_require_truthy=False),
            timeout=timeout,
            step_name=step_name,
        )

    _main_logger.info(t("startup.shutdown_completed"))


async def _run_restart_partial() -> None:
    """复现 WebUI 重启前清理：emit(ON_STOP) → runtime.stop → tasks。"""

    for step in MODE_STEPS["restart_partial"]:
        try:
            await _invoke_step(step, webui_server=None, webui_require_truthy=False)
        except Exception as exc:
            level, template, exc_info = _RESTART_PARTIAL_ERRORS[step]
            message = template.format(exc=exc)
            if level == "error":
                _webui_logger.error(message, exc_info=exc_info)
            else:
                _webui_logger.warning(message, exc_info=exc_info)


async def _await_shutdown_step(awaitable: Awaitable[Any], *, timeout: float, step_name: str) -> Any:
    """为关停步骤设置硬超时，避免单个组件阻塞 Ctrl+C 退出。"""

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        _main_logger.warning(f"{step_name} 超时，继续执行后续关停步骤")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _main_logger.warning(f"{step_name} 失败，继续执行后续关停步骤: {exc}", exc_info=True)
        return None


async def _invoke_step(
    step: ShutdownStep,
    *,
    webui_server: SupportsAsyncShutdown | None,
    webui_require_truthy: bool,
) -> None:
    """执行单个命名步骤。BRIDGE_ON_STOP 与 EMIT_ON_STOP 不能互相替代。"""

    match step:
        case ShutdownStep.REQUEST_SHUTDOWN:
            from src.common.shutdown import request_shutdown

            request_shutdown("graceful_shutdown")
        case ShutdownStep.WEBUI:
            await _shutdown_webui(webui_server, require_truthy=webui_require_truthy)
        case ShutdownStep.EMOJI:
            from src.emoji_system.emoji_manager import emoji_manager

            emoji_manager.shutdown()
        case ShutdownStep.MEMORY_AUTOMATION:
            from src.services.memory_flow_service import memory_automation_service

            await memory_automation_service.shutdown()
        case ShutdownStep.MEMORIX:
            from src.A_memorix.host_service import a_memorix_host_service

            await a_memorix_host_service.stop()
        case ShutdownStep.BRIDGE_ON_STOP:
            from src.plugin_runtime.integration import get_plugin_runtime_manager

            await get_plugin_runtime_manager().bridge_event("on_stop")
        case ShutdownStep.EMIT_ON_STOP:
            from .event_bus import event_bus
            from .types import EventType

            await event_bus.emit(event_type=EventType.ON_STOP)
        case ShutdownStep.RUNTIME_STOP:
            from src.plugin_runtime.integration import get_plugin_runtime_manager

            await get_plugin_runtime_manager().stop()
        case ShutdownStep.TASKS:
            from src.manager.async_task_manager import async_task_manager

            await async_task_manager.stop_and_wait_all_tasks()
        case ShutdownStep.MCP:
            from src.mcp_module.service import get_mcp_service

            await get_mcp_service().close()
        case ShutdownStep.FILE_WATCHER:
            from src.config.config import config_manager

            await config_manager.stop_file_watcher()
        case ShutdownStep.CLEAR_MAIN_LOOP:
            from src.common.runtime_loop import set_main_loop

            set_main_loop(None)
        case ShutdownStep.CANCEL_REMAINING_TASKS:
            await _cancel_remaining_tasks()
        case _:
            raise ValueError(f"未知停机步骤: {step}")


async def _shutdown_webui(
    webui_server: SupportsAsyncShutdown | None,
    *,
    require_truthy: bool,
) -> None:
    """关闭 WebUI。full 用真值判断，stepwise_timeout 用 is not None。"""

    if require_truthy:
        if webui_server:
            await webui_server.shutdown()
        return
    if webui_server is not None:
        await webui_server.shutdown()


async def _cancel_remaining_tasks() -> None:
    """取消当前事件循环中除关停任务外的剩余任务。"""

    remaining_tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    if not remaining_tasks:
        return

    _main_logger.info(tn("startup.remaining_tasks_cancelling", len(remaining_tasks)))
    for task in remaining_tasks:
        if not task.done():
            task.cancel()

    try:
        await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=5.0)
        _main_logger.info(t("startup.remaining_tasks_cancelled"))
    except asyncio.TimeoutError:
        _main_logger.warning(t("startup.remaining_tasks_cancel_timeout"))
    except Exception as e:
        _main_logger.error(t("startup.remaining_tasks_cancel_error", error=e))

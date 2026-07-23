"""进程级 MCP 运行时服务。

该服务在主事件循环中统一持有 MCP 连接，聊天 Runtime 只通过轻量 Provider
访问共享连接，避免每个聊天流重复启动 stdio 子进程和远程 HTTP 会话。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Sequence

import asyncio
import json
import threading

from src.common.logger import get_logger
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .config import build_mcp_client_runtime_config, build_mcp_server_runtime_configs
from .host_llm_bridge import MCPHostLLMBridge
from .manager import MCPManager

if TYPE_CHECKING:
    from src.config.official_configs import MCPConfig

logger = get_logger("mcp_service")


class MCPService:
    """在进程生命周期内复用 MCP 连接并支持安全热切换。"""

    def __init__(self) -> None:
        self._manager: Optional[MCPManager] = None
        self._config_signature = ""
        self._reload_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._active_leases: dict[int, int] = {}
        self._retired_managers: dict[int, MCPManager] = {}
        self._background_reload_tasks: set[asyncio.Task[None]] = set()
        self._reload_callback_registered = False
        self._closed = False
        self._status_lock = threading.Lock()
        self._status_snapshot: dict[str, Any] = {
            "initialized": False,
            "server_count": 0,
            "tool_count": 0,
            "servers": [],
        }

    @staticmethod
    def _build_config_signature(mcp_config: "MCPConfig") -> str:
        """仅按有效运行时配置生成签名，停用草稿变化不会重启连接。"""

        server_configs = build_mcp_server_runtime_configs(mcp_config)
        client_config = build_mcp_client_runtime_config(mcp_config)
        return json.dumps(
            {
                "servers": [asdict(server_config) for server_config in server_configs],
                "client": (
                    {
                        "client_name": client_config.client_name,
                        "client_version": client_config.client_version,
                        "roots": (
                            [asdict(root_config) for root_config in client_config.roots]
                            if client_config.enable_roots
                            else []
                        ),
                        "sampling": (
                            {
                                "task_name": client_config.sampling_task_name,
                                "tool_support": client_config.sampling_tool_support,
                            }
                            if client_config.enable_sampling
                            else None
                        ),
                    }
                    if server_configs
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def register_config_reload_callback(self) -> None:
        """注册主配置热重载回调；重复调用不会重复注册。"""

        if self._reload_callback_registered:
            return

        from src.config.config import config_manager

        config_manager.register_reload_callback(self.on_config_reload)
        self._reload_callback_registered = True

    async def on_config_reload(self, changed_scopes: Sequence[str] | None = None) -> None:
        """在 bot 配置变化后按有效配置重建 MCP 连接。"""

        normalized_scopes = {str(scope).strip().lower() for scope in changed_scopes or ("bot",)}
        if "bot" not in normalized_scopes:
            return

        from src.config.config import config_manager

        self._schedule_reload(config_manager.get_global_config().mcp)

    def _schedule_reload(self, mcp_config: "MCPConfig") -> None:
        """后台执行热重载，避免阻塞配置文件监听器。"""

        reload_task = asyncio.create_task(
            self.reload(mcp_config),
            name="mcp_config_reload",
        )
        self._background_reload_tasks.add(reload_task)
        reload_task.add_done_callback(self._handle_background_reload_done)

    def _handle_background_reload_done(self, task: asyncio.Task[None]) -> None:
        """回收后台热重载任务并记录真实异常。"""

        self._background_reload_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            logger.exception(f"MCP 配置热重载失败: {exc}")

    async def ensure_initialized(self, mcp_config: "MCPConfig") -> None:
        """确保共享服务已按当前配置初始化。"""

        self.register_config_reload_callback()
        await self.reload(mcp_config)

    async def reload(self, mcp_config: "MCPConfig") -> None:
        """构建新一代连接并原子替换旧管理器。"""

        signature = self._build_config_signature(mcp_config)
        async with self._reload_lock:
            if not self._closed and signature == self._config_signature:
                return

            self._closed = False
            new_manager: Optional[MCPManager] = None
            if build_mcp_server_runtime_configs(mcp_config):
                host_callbacks = (
                    MCPHostLLMBridge(
                        sampling_task_name=mcp_config.client.sampling.task_name,
                    ).build_callbacks()
                    if mcp_config.client.sampling.enable
                    else None
                )
                new_manager = await MCPManager.from_app_config(
                    mcp_config,
                    host_callbacks=host_callbacks,
                    discover_extended_features=False,
                    allow_empty=True,
                )

            async with self._state_lock:
                old_manager = self._manager
                self._manager = new_manager
                self._config_signature = signature
                close_immediately = self._retire_manager_locked(old_manager)

            self._update_status_snapshot(new_manager)
            if close_immediately is not None:
                await close_immediately.close()

            if new_manager is None:
                logger.info("MCP 共享服务当前没有启用的服务器")
            else:
                logger.info(
                    f"MCP 共享服务已加载：服务器 {new_manager.server_count} 个，工具 {new_manager.tool_count} 个"
                )

    def _retire_manager_locked(self, manager: Optional[MCPManager]) -> Optional[MCPManager]:
        """在持有状态锁时退休旧管理器，并决定是否可立即关闭。"""

        if manager is None:
            return None
        manager_id = id(manager)
        if self._active_leases.get(manager_id, 0) > 0:
            self._retired_managers[manager_id] = manager
            return None
        return manager

    @asynccontextmanager
    async def _lease_manager(self) -> AsyncIterator[Optional[MCPManager]]:
        """租用当前管理器，防止热重载关闭仍在执行工具的连接。"""

        manager: Optional[MCPManager]
        manager_id = 0
        async with self._state_lock:
            manager = self._manager
            if manager is not None:
                manager_id = id(manager)
                self._active_leases[manager_id] = self._active_leases.get(manager_id, 0) + 1

        try:
            yield manager
        finally:
            manager_to_close: Optional[MCPManager] = None
            if manager is not None:
                async with self._state_lock:
                    lease_count = self._active_leases.get(manager_id, 0) - 1
                    if lease_count > 0:
                        self._active_leases[manager_id] = lease_count
                    else:
                        self._active_leases.pop(manager_id, None)
                        manager_to_close = self._retired_managers.pop(manager_id, None)
            if manager_to_close is not None:
                await manager_to_close.close()

    async def list_tools(self) -> list[ToolSpec]:
        """列出共享管理器当前可用的工具。"""

        async with self._lease_manager() as manager:
            return manager.get_tool_specs() if manager is not None else []

    async def call_tool_invocation(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """调用共享 MCP 工具，并保留聊天执行上下文用于审计。"""

        async with self._lease_manager() as manager:
            if manager is None:
                return ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=False,
                    error_message="MCP 共享服务尚未连接任何服务器",
                )
            result = await manager.call_tool_invocation(invocation)

        if context is not None:
            result.metadata.setdefault("chat_session_id", context.session_id)
            result.metadata.setdefault("chat_stream_id", context.stream_id)
        return result

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回不含异步连接对象的线程安全状态副本。"""

        with self._status_lock:
            return json.loads(json.dumps(self._status_snapshot, ensure_ascii=False))

    def _update_status_snapshot(
        self,
        manager: Optional[MCPManager],
        *,
        initialized: bool = True,
    ) -> None:
        snapshot = (
            manager.get_status_snapshot()
            if manager is not None
            else {
                "initialized": initialized,
                "server_count": 0,
                "tool_count": 0,
                "servers": [],
            }
        )
        with self._status_lock:
            self._status_snapshot = snapshot

    async def close(self) -> None:
        """关闭当前及已退休的全部管理器。"""

        pending_reload_tasks = [
            task for task in self._background_reload_tasks if not task.done()
        ]
        if pending_reload_tasks:
            await asyncio.gather(*pending_reload_tasks, return_exceptions=True)

        async with self._reload_lock:
            async with self._state_lock:
                current_manager = self._manager
                self._manager = None
                managers = list(self._retired_managers.values())
                self._retired_managers.clear()
                if current_manager is not None:
                    managers.append(current_manager)
                self._config_signature = ""
                self._closed = True

            if managers:
                await asyncio.gather(*(manager.close() for manager in managers), return_exceptions=True)
            self._update_status_snapshot(None, initialized=False)


_mcp_service = MCPService()


def get_mcp_service() -> MCPService:
    """获取进程级 MCP 服务单例。"""

    return _mcp_service

"""Runner 主循环

作为独立子进程运行，负责：
1. 从环境变量读取 IPC 地址和会话令牌
2. 连接 Host 并完成握手
3. 加载所有插件
4. 注册组件到 Host
5. 处理 Host 的调用请求
6. 转发插件的能力调用到 Host
"""

import asyncio
import contextlib
import inspect
import os
import signal
import sys
import time

from src.common.logger import get_logger, initialize_logging
from src.plugin_runtime.protocol.envelope import (
    ComponentDeclaration,
    Envelope,
    HealthPayload,
    InvokePayload,
    InvokeResultPayload,
    RegisterComponentsPayload,
)
from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.runner.plugin_loader import PluginLoader, PluginMeta
from src.plugin_runtime.runner.rpc_client import RPCClient

logger = get_logger("plugin_runtime.runner.main")


class PluginRunner:
    """插件 Runner

    运行在独立子进程中，管理所有插件的执行。
    """

    def __init__(
        self,
        host_address: str,
        session_token: str,
        plugin_dirs: list[str],
    ) -> None:
        self._host_address: str = host_address
        self._session_token: str = session_token
        self._plugin_dirs: list[str] = plugin_dirs

        self._rpc_client: RPCClient = RPCClient(host_address, session_token)
        self._loader: PluginLoader = PluginLoader()
        self._start_time: float = time.monotonic()
        self._shutting_down: bool = False

    async def run(self) -> None:
        """Runner 主入口"""
        # 1. 连接 Host
        logger.info(f"Runner 启动，连接 Host: {self._host_address}")
        ok = await self._rpc_client.connect_and_handshake()
        if not ok:
            logger.error("握手失败，退出")
            return

        # 2. 注册方法处理器
        self._register_handlers()

        # 3. 加载插件
        plugins = self._loader.discover_and_load(self._plugin_dirs)
        logger.info(f"已加载 {len(plugins)} 个插件")

        # 4. 调用 on_load 生命周期钩子 + 注入 RPC 客户端供 SDK context 使用
        for meta in plugins:
            instance = meta.instance
            # 注入 _rpc_client 以便 PluginContext 可以发起能力调用
            if hasattr(instance, "_ctx"):
                ctx = instance._ctx
                if hasattr(ctx, "_set_rpc_client"):
                    ctx._set_rpc_client(self._rpc_client)
            if hasattr(instance, "on_load"):
                try:
                    ret = instance.on_load()
                    if asyncio.iscoroutine(ret):
                        await ret
                except Exception as e:
                    logger.error(f"插件 {meta.plugin_id} on_load 失败: {e}", exc_info=True)

        # 5. 向 Host 注册所有插件的组件
        for meta in plugins:
            await self._register_plugin(meta)

        # 5. 等待直到收到关停信号
        with contextlib.suppress(asyncio.CancelledError):
            while not self._shutting_down:
                await asyncio.sleep(1.0)

        # 6. 断开连接
        await self._rpc_client.disconnect()
        logger.info("Runner 已退出")

    def _register_handlers(self) -> None:
        """注册方法处理器"""
        self._rpc_client.register_method("plugin.invoke_command", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_action", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_tool", self._handle_invoke)
        self._rpc_client.register_method("plugin.emit_event", self._handle_invoke)
        self._rpc_client.register_method("plugin.invoke_workflow_step", self._handle_workflow_step)
        self._rpc_client.register_method("plugin.health", self._handle_health)
        self._rpc_client.register_method("plugin.prepare_shutdown", self._handle_prepare_shutdown)
        self._rpc_client.register_method("plugin.shutdown", self._handle_shutdown)
        self._rpc_client.register_method("plugin.config_updated", self._handle_config_updated)

    async def _register_plugin(self, meta: PluginMeta) -> None:
        """向 Host 注册单个插件"""
        # 收集插件组件声明
        components: list[ComponentDeclaration] = []
        instance = meta.instance

        # 从插件实例获取组件声明（SDK 插件须实现 get_components 方法）
        if hasattr(instance, "get_components"):
            components.extend(
                ComponentDeclaration(
                    name=comp_info.get("name", ""),
                    component_type=comp_info.get("type", ""),
                    plugin_id=meta.plugin_id,
                    metadata=comp_info.get("metadata", {}),
                )
                for comp_info in instance.get_components()
            )

        reg_payload = RegisterComponentsPayload(
            plugin_id=meta.plugin_id,
            plugin_version=meta.version,
            components=components,
            capabilities_required=meta.capabilities_required,
        )

        try:
            _resp = await self._rpc_client.send_request(
                "plugin.register_components",
                plugin_id=meta.plugin_id,
                payload=reg_payload.model_dump(),
                timeout_ms=10000,
            )
            logger.info(f"插件 {meta.plugin_id} 注册完成")
        except Exception as e:
            logger.error(f"插件 {meta.plugin_id} 注册失败: {e}")

    async def _handle_invoke(self, envelope: Envelope) -> Envelope:
        """处理组件调用请求"""
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(e))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        # 调用插件实例的组件方法
        instance = meta.instance
        component_name = invoke.component_name

        handler_method = getattr(instance, f"handle_{component_name}", None)
        if handler_method is None:
            handler_method = getattr(instance, component_name, None)

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            result = await handler_method(**invoke.args) if inspect.iscoroutinefunction(handler_method) else handler_method(**invoke.args)
            resp_payload = InvokeResultPayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except Exception as e:
            logger.error(f"插件 {plugin_id} 组件 {component_name} 执行异常: {e}", exc_info=True)
            resp_payload = InvokeResultPayload(success=False, result=str(e))
            return envelope.make_response(payload=resp_payload.model_dump())

    async def _handle_workflow_step(self, envelope: Envelope) -> Envelope:
        """处理 WorkflowStep 调用请求

        与通用 invoke 不同，会将返回值规范化为
        {hook_result, modified_message, stage_output} 格式。
        """
        try:
            invoke = InvokePayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(e))

        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta is None:
            return envelope.make_error_response(
                ErrorCode.E_PLUGIN_NOT_FOUND.value,
                f"插件 {plugin_id} 未加载",
            )

        instance = meta.instance
        component_name = invoke.component_name
        handler_method = getattr(instance, f"handle_{component_name}", None) or getattr(instance, component_name, None)

        if handler_method is None or not callable(handler_method):
            return envelope.make_error_response(
                ErrorCode.E_METHOD_NOT_ALLOWED.value,
                f"插件 {plugin_id} 无组件: {component_name}",
            )

        try:
            raw = await handler_method(**invoke.args) if inspect.iscoroutinefunction(handler_method) else handler_method(**invoke.args)

            # 规范化返回值
            if isinstance(raw, str):
                result = {"hook_result": raw}
            elif isinstance(raw, dict):
                result = raw
                result.setdefault("hook_result", "continue")
            else:
                result = {"hook_result": "continue"}

            resp_payload = InvokeResultPayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except Exception as e:
            logger.error(f"插件 {plugin_id} workflow_step {component_name} 执行异常: {e}", exc_info=True)
            resp_payload = InvokeResultPayload(success=False, result=str(e))
            return envelope.make_response(payload=resp_payload.model_dump())

    async def _handle_health(self, envelope: Envelope) -> Envelope:
        """处理健康检查"""
        uptime_ms = int((time.monotonic() - self._start_time) * 1000)
        health = HealthPayload(
            healthy=True,
            loaded_plugins=self._loader.list_plugins(),
            uptime_ms=uptime_ms,
        )
        return envelope.make_response(payload=health.model_dump())

    async def _handle_prepare_shutdown(self, envelope: Envelope) -> Envelope:
        """处理准备关停"""
        logger.info("收到 prepare_shutdown 信号")
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_shutdown(self, envelope: Envelope) -> Envelope:
        """处理关停 — 调用所有插件的 on_unload 后退出"""
        logger.info("收到 shutdown 信号，开始调用 on_unload")
        for plugin_id in self._loader.list_plugins():
            meta = self._loader.get_plugin(plugin_id)
            if meta and hasattr(meta.instance, "on_unload"):
                try:
                    ret = meta.instance.on_unload()
                    if asyncio.iscoroutine(ret):
                        await ret
                except Exception as e:
                    logger.error(f"插件 {plugin_id} on_unload 失败: {e}", exc_info=True)
        self._shutting_down = True
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_config_updated(self, envelope: Envelope) -> Envelope:
        """处理配置更新事件"""
        plugin_id = envelope.plugin_id
        meta = self._loader.get_plugin(plugin_id)
        if meta and hasattr(meta.instance, "on_config_update"):
            try:
                config_data = envelope.payload.get("config_data", {})
                config_version = envelope.payload.get("config_version", "")
                await meta.instance.on_config_update(config_data, config_version)
            except Exception as e:
                logger.error(f"插件 {plugin_id} 配置更新失败: {e}")
                return envelope.make_error_response(ErrorCode.E_UNKNOWN.value, str(e))
        return envelope.make_response(payload={"acknowledged": True})

    def request_capability(self) -> RPCClient:
        """获取 RPC 客户端（供 SDK 使用，发起能力调用）"""
        return self._rpc_client


# ─── sys.path 隔离 ────────────────────────────────────────

def _isolate_sys_path(plugin_dirs: list[str]) -> None:
    """清理 sys.path，限制 Runner 子进程只能访问标准库、SDK 和插件目录。

    防止插件代码 import 主程序模块读取运行时数据。
    """
    import sysconfig

    # 保留: 标准库路径 + site-packages（含 SDK 和依赖）
    stdlib_paths = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        if path := sysconfig.get_path(key):
            stdlib_paths.add(os.path.normpath(path))

    allowed = set()
    for p in sys.path:
        norm = os.path.normpath(p)
        # 保留标准库和 site-packages
        if any(norm.startswith(sp) for sp in stdlib_paths):
            allowed.add(p)
        # 保留 site-packages（第三方库 + SDK）
        if "site-packages" in norm or "dist-packages" in norm:
            allowed.add(p)

    # 添加插件目录
    for d in plugin_dirs:
        allowed.add(os.path.normpath(d))

    # 添加当前 runner 模块所在路径（使得 src.plugin_runtime 可导入）
    runtime_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    allowed.add(runtime_root)

    sys.path[:] = [p for p in sys.path if p in allowed]


# ─── 进程入口 ──────────────────────────────────────────────

async def _async_main() -> None:
    """异步主入口"""
    host_address = os.environ.get("MAIBOT_IPC_ADDRESS", "")
    session_token = os.environ.get("MAIBOT_SESSION_TOKEN", "")
    plugin_dirs_str = os.environ.get("MAIBOT_PLUGIN_DIRS", "")

    if not host_address or not session_token:
        logger.error("缺少必要的环境变量: MAIBOT_IPC_ADDRESS, MAIBOT_SESSION_TOKEN")
        sys.exit(1)

    plugin_dirs = [d for d in plugin_dirs_str.split(os.pathsep) if d]

    # sys.path 隔离: 只保留标准库、SDK 包、插件目录
    _isolate_sys_path(plugin_dirs)

    runner = PluginRunner(host_address, session_token, plugin_dirs)

    # 注册信号处理
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(runner, "_shutting_down", True))

    await runner.run()


def main() -> None:
    """进程入口（python -m src.plugin_runtime.runner.runner_main）"""
    initialize_logging(verbose=False)
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()

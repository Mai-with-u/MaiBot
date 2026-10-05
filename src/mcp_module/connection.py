"""
MaiSaka - 单个 MCP 服务器连接管理
封装单个 MCP 服务器的连接生命周期：连接 → 发现能力 → 调用工具/读取资源 → 断开。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable, Optional, cast

import asyncio
import httpx

from src.cli.console import console
from src.common.logger import get_logger
from src.core.tooling import ToolExecutionResult

from .config import MCPClientRuntimeConfig, MCPServerRuntimeConfig
from .hooks import MCPHostCallbacks
from .models import (
    MCPPromptResult,
    MCPResourceReadResult,
    build_prompt_result,
    build_resource_read_result,
    build_tool_content_items,
)

if TYPE_CHECKING:
    from mcp.client.session import ElicitationFnT, ListRootsFnT, LoggingFnT, MessageHandlerFnT, SamplingFnT

# ──────────────────── MCP SDK 可选导入 ────────────────────
#
# mcp 是可选依赖。如果未安装，MCP_AVAILABLE = False，
# MCPManager.from_app_config() 会检测到并返回 None，不影响主程序运行。

try:
    from mcp import ClientSession, types as mcp_types

    try:
        from mcp.client.stdio import StdioServerParameters
    except ImportError:
        from mcp import StdioServerParameters  # type: ignore[attr-defined]

    from mcp.shared.exceptions import McpError

    try:
        from .stdio_filter import tolerant_stdio_client
    except ImportError:
        tolerant_stdio_client = None  # type: ignore[assignment]

    try:
        from mcp.client.streamable_http import streamable_http_client

        STREAMABLE_HTTP_AVAILABLE = True
        STREAMABLE_HTTP_USES_LEGACY_CLIENT = False
    except ImportError:
        try:
            from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

            STREAMABLE_HTTP_AVAILABLE = True
            STREAMABLE_HTTP_USES_LEGACY_CLIENT = True
        except ImportError:
            STREAMABLE_HTTP_AVAILABLE = False
            STREAMABLE_HTTP_USES_LEGACY_CLIENT = False
            streamable_http_client = None  # type: ignore[assignment]

    try:
        from mcp.client.sse import sse_client

        SSE_AVAILABLE = True
    except ImportError:
        SSE_AVAILABLE = False
        sse_client = None  # type: ignore[assignment]

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    STREAMABLE_HTTP_AVAILABLE = False
    STREAMABLE_HTTP_USES_LEGACY_CLIENT = False
    SSE_AVAILABLE = False
    ClientSession = None  # type: ignore[assignment,misc]
    StdioServerParameters = None  # type: ignore[assignment,misc]
    mcp_types = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    sse_client = None  # type: ignore[assignment]
    tolerant_stdio_client = None  # type: ignore[assignment]
    McpError = Exception  # type: ignore[assignment,misc]

logger = get_logger("mcp_connection")

# 等待连接所有者任务正常关闭的最短时限：stdio 传输关闭时会等待子进程退出、必要时再终止进程树，
# 正常情况下数秒内即可完成
MIN_CLOSE_TIMEOUT_SECONDS = 10.0


def _format_exception_message(exc: BaseException) -> str:
    """提取异常的可读信息。

    anyio 的 TaskGroup 会把子任务异常包装为异常组，这里展开为其中的原始异常信息。

    Args:
        exc: 需要描述的异常。

    Returns:
        str: 可读的异常信息。
    """

    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_format_exception_message(sub_exception) for sub_exception in exc.exceptions)
    return str(exc).strip() or exc.__class__.__name__


@dataclass(slots=True)
class _ConnectionLifecycle:
    """单次连接的所有者任务及其同步原语。"""

    task: "asyncio.Task[None]"
    ready: "asyncio.Future[None]"
    close_requested: asyncio.Event


class MCPConnection:
    """管理单个 MCP 服务器的连接生命周期。

    传输层与 ``ClientSession`` 内部使用 anyio 的 TaskGroup / CancelScope，anyio 要求它们必须在
    进入时所在的同一个任务里退出。因此每个连接由一个常驻的所有者任务负责完整生命周期：在该任务内
    进入传输与会话、完成初始化，然后等待关闭信号，最后仍在该任务内退出全部上下文。``connect()``
    与 ``close()`` 可以在任意任务中调用，它们只负责启动、通知并等待所有者任务。
    """

    def __init__(
        self,
        config: MCPServerRuntimeConfig,
        client_config: MCPClientRuntimeConfig,
        host_callbacks: Optional[MCPHostCallbacks] = None,
        *,
        discover_extended_features: bool = True,
    ) -> None:
        """初始化单个 MCP 连接。

        Args:
            config: 当前服务器的运行时配置。
            client_config: MCP 客户端宿主能力运行时配置。
            host_callbacks: 宿主侧能力回调集合。
            discover_extended_features: 是否在连接时发现 Prompt 和 Resource。
        """

        self.config = config
        self.client_config = client_config
        self.host_callbacks = host_callbacks or MCPHostCallbacks()
        self.discover_extended_features = discover_extended_features

        self.session: Optional[Any] = None
        self.server_capabilities: Optional[Any] = None
        self.tools: list[Any] = []
        self.prompts: list[Any] = []
        self.resources: list[Any] = []
        self.resource_templates: list[Any] = []
        self.protocol_version: str = ""
        self.last_error: str = ""

        self._http_client: Optional[httpx.AsyncClient] = None
        self._session_id_getter: Optional[Callable[[], str | None]] = None
        self._lifecycle: Optional[_ConnectionLifecycle] = None

    @property
    def session_id(self) -> str:
        """返回当前连接协商得到的 MCP 会话标识。

        Returns:
            str: 当前会话 ID；无会话时返回空字符串。
        """

        if self._session_id_getter is None:
            return ""
        return self._session_id_getter() or ""

    async def connect(self) -> bool:
        """连接到 MCP 服务器并发现可用能力。

        建连在独立的所有者任务中完成，当前任务只等待其就绪或失败。建连被取消（例如外层超时）时，
        会先让所有者任务在自身任务内回滚已进入的上下文，再继续向上传播取消。

        Returns:
            bool: `True` 表示连接成功，`False` 表示失败。
        """

        if not MCP_AVAILABLE:
            self.last_error = "未安装 mcp SDK"
            console.print("[warning]⚠️ 未安装 mcp SDK，请运行: pip install mcp[/warning]")
            return False

        if self.session is not None:
            return True

        if self._lifecycle is not None:
            if not self._lifecycle.task.done():
                raise RuntimeError(f"MCP 服务器 '{self.config.name}' 的连接正在建立中，不能重复连接")
            # 上一次连接已在运行期异常中断，所有者任务已结束并记录过中断原因
            self._lifecycle = None

        self.last_error = ""
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        close_requested = asyncio.Event()
        lifecycle = _ConnectionLifecycle(
            task=asyncio.create_task(
                self._run_lifecycle(ready, close_requested),
                name=f"mcp_connection:{self.config.name}",
            ),
            ready=ready,
            close_requested=close_requested,
        )
        self._lifecycle = lifecycle
        try:
            await asyncio.wait((lifecycle.ready, lifecycle.task), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # 建连被取消（如外层超时）：先让所有者任务在自身任务内回滚已进入的上下文，再继续传播取消
            await self.close()
            raise

        if lifecycle.close_requested.is_set():
            # 建连期间已被 close()：所有者任务的回收与异常记录由 close() 负责
            self.last_error = "连接在建立过程中被关闭"
            return False
        if lifecycle.ready.done():
            return True

        # 所有者任务在就绪前结束即建连失败，已进入的上下文已在所有者任务内退出
        self._lifecycle = None
        try:
            lifecycle.task.result()
        except Exception as exc:
            self.last_error = _format_exception_message(exc)
            logger.error(f"MCP 服务器 '{self.config.name}' 连接失败: {self.last_error}", exc_info=exc)
            return False
        raise RuntimeError(f"MCP 服务器 '{self.config.name}' 的连接任务在就绪前意外结束")

    async def _run_lifecycle(self, ready: "asyncio.Future[None]", close_requested: asyncio.Event) -> None:
        """连接所有者任务：在同一任务内进入并退出传输与会话的全部异步上下文。

        Args:
            ready: 建连与能力发现完成后置为完成，用于通知 ``connect()``。
            close_requested: ``close()`` 发出的关闭信号。

        Raises:
            Exception: 建连阶段与关闭阶段的异常，分别交给等待中的 ``connect()`` 与 ``close()`` 记录。
        """

        try:
            async with AsyncExitStack() as exit_stack:
                try:
                    read_stream, write_stream = await self._connect_transport(exit_stack)
                    session = await self._create_client_session(exit_stack, read_stream, write_stream)
                    self.session = session
                    initialize_result = await session.initialize()
                    self.server_capabilities = getattr(initialize_result, "capabilities", None)
                    self.protocol_version = str(getattr(initialize_result, "protocolVersion", "") or "")

                    await self._load_server_features()
                except Exception:
                    # 建连失败：先在本任务内按正常路径退出已进入的上下文，再把原始异常交给 connect()；
                    # 若退出时再次失败，建连异常会作为新异常的上下文一并保留
                    await exit_stack.aclose()
                    raise

                ready.set_result(None)
                await close_requested.wait()
        except Exception as exc:
            if not ready.done() or close_requested.is_set():
                raise
            # 运行期传输异常（如子进程输出无法解码、远程流断开）没有调用方在等待，必须在此完整记录
            self.last_error = _format_exception_message(exc)
            logger.error(f"MCP 服务器 '{self.config.name}' 连接异常中断: {self.last_error}", exc_info=exc)
        finally:
            self._reset_connection_state()

    async def _connect_transport(self, exit_stack: AsyncExitStack) -> tuple[Any, Any]:
        """根据配置建立底层传输连接。

        Args:
            exit_stack: 所有者任务持有的上下文栈。

        Returns:
            tuple[Any, Any]: 读写流对象。
        """

        if self.config.transport_type == "stdio":
            return await self._connect_stdio(exit_stack)
        if self.config.transport_type == "streamable_http":
            return await self._connect_streamable_http(exit_stack)
        if self.config.transport_type == "sse":
            return await self._connect_sse(exit_stack)

        raise ValueError(f"MCP 服务器 '{self.config.name}' 使用了未知传输类型: {self.config.transport}")

    async def _connect_stdio(self, exit_stack: AsyncExitStack) -> tuple[Any, Any]:
        """建立 stdio 传输连接。

        Args:
            exit_stack: 所有者任务持有的上下文栈。

        Returns:
            tuple[Any, Any]: 读写流对象。
        """

        if StdioServerParameters is None or tolerant_stdio_client is None:
            raise RuntimeError("当前环境未安装可用的 MCP stdio 客户端")
        if not self.config.command:
            raise ValueError(f"MCP 服务器 '{self.config.name}' 缺少 stdio command 配置")

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env,
        )
        # 容错包装：丢弃违规 server 写到 stdout 的非 JSON 噪声以防 initialize 失败；详见 stdio_filter.py。
        return await exit_stack.enter_async_context(tolerant_stdio_client(params))

    async def _connect_streamable_http(self, exit_stack: AsyncExitStack) -> tuple[Any, Any]:
        """建立 Streamable HTTP 传输连接。

        Args:
            exit_stack: 所有者任务持有的上下文栈。

        Returns:
            tuple[Any, Any]: 读写流对象。
        """

        if not STREAMABLE_HTTP_AVAILABLE or streamable_http_client is None:
            raise ImportError("当前环境未安装可用的 MCP Streamable HTTP 客户端")
        if not self.config.url:
            raise ValueError(f"MCP 服务器 '{self.config.name}' 缺少 Streamable HTTP url 配置")

        if STREAMABLE_HTTP_USES_LEGACY_CLIENT:
            read_stream, write_stream, session_id_getter = await exit_stack.enter_async_context(
                streamable_http_client(
                    url=self.config.url,
                    headers=self.config.build_http_headers(),
                    timeout=self.config.http_timeout_seconds,
                    sse_read_timeout=self.config.read_timeout_seconds,
                    terminate_on_close=True,
                    httpx_client_factory=self._build_http_client,
                )
            )
        else:
            self._http_client = await exit_stack.enter_async_context(self._build_http_client())
            read_stream, write_stream, session_id_getter = await exit_stack.enter_async_context(
                streamable_http_client(
                    url=self.config.url,
                    http_client=self._http_client,
                    terminate_on_close=True,
                )
            )
        self._session_id_getter = session_id_getter
        return read_stream, write_stream

    async def _connect_sse(self, exit_stack: AsyncExitStack) -> tuple[Any, Any]:
        """建立 SSE 传输连接。

        Args:
            exit_stack: 所有者任务持有的上下文栈。

        Returns:
            tuple[Any, Any]: 读写流对象。
        """

        if not SSE_AVAILABLE or sse_client is None:
            raise ImportError("当前环境未安装可用的 MCP SSE 客户端")
        if not self.config.url:
            raise ValueError(f"MCP 服务器 '{self.config.name}' 缺少 SSE url 配置")

        read_stream, write_stream = await exit_stack.enter_async_context(
            sse_client(
                url=self.config.url,
                headers=self.config.build_http_headers(),
                timeout=self.config.http_timeout_seconds,
                sse_read_timeout=self.config.read_timeout_seconds,
                httpx_client_factory=self._build_http_client,
            )
        )
        return read_stream, write_stream

    def _build_http_client(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """构建 httpx 客户端。

        Args:
            headers: 合并到配置请求头的额外请求头。
            timeout: 覆盖的 httpx 超时配置。
            auth: 附加认证。

        Returns:
            httpx.AsyncClient: 预配置的异步 HTTP 客户端。
        """

        del auth
        merged_headers = self.config.build_http_headers()
        if headers:
            merged_headers.update(headers)
        return httpx.AsyncClient(
            headers=merged_headers,
            timeout=timeout
            or httpx.Timeout(
                connect=self.config.http_timeout_seconds,
                read=self.config.read_timeout_seconds,
                write=self.config.http_timeout_seconds,
                pool=self.config.http_timeout_seconds,
            ),
        )

    async def _create_client_session(self, exit_stack: AsyncExitStack, read_stream: Any, write_stream: Any) -> Any:
        """创建并返回 MCP `ClientSession`。

        Args:
            exit_stack: 所有者任务持有的上下文栈。
            read_stream: 底层读取流。
            write_stream: 底层写入流。

        Returns:
            Any: 已初始化的 MCP `ClientSession` 实例。
        """

        if ClientSession is None:
            raise RuntimeError("当前环境未安装可用的 MCP ClientSession")

        list_roots_callback = self._build_list_roots_callback()
        sampling_callback = (
            self.host_callbacks.sampling_callback
            if self.client_config.enable_sampling and self.host_callbacks.sampling_callback is not None
            else None
        )
        elicitation_callback = (
            self.host_callbacks.elicitation_callback
            if self.client_config.enable_elicitation and self.host_callbacks.elicitation_callback is not None
            else None
        )
        logging_callback = cast(Optional["LoggingFnT"], self.host_callbacks.logging_callback)
        message_handler = cast(Optional["MessageHandlerFnT"], self.host_callbacks.message_handler)

        if self.client_config.enable_sampling and sampling_callback is None:
            console.print(
                f"[warning]⚠️ MCP 服务器 '{self.config.name}' 已启用 sampling 配置，但宿主未提供 sampling 回调，当前不会声明该能力[/warning]"
            )
        if self.client_config.enable_elicitation and elicitation_callback is None:
            console.print(
                f"[warning]⚠️ MCP 服务器 '{self.config.name}' 已启用 elicitation 配置，但宿主未提供 elicitation 回调，当前不会声明该能力[/warning]"
            )

        session = await exit_stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.config.read_timeout_seconds),
                sampling_callback=cast(Optional["SamplingFnT"], sampling_callback),
                elicitation_callback=cast(Optional["ElicitationFnT"], elicitation_callback),
                list_roots_callback=cast(Optional["ListRootsFnT"], list_roots_callback),
                logging_callback=logging_callback,
                message_handler=message_handler,
                client_info=self._build_client_info(),
                sampling_capabilities=self._build_sampling_capabilities(sampling_callback),
            )
        )
        return session

    def _build_client_info(self) -> Any:
        """构建 MCP 客户端实现信息。

        Returns:
            Any: MCP SDK 的 `Implementation` 对象。
        """

        if mcp_types is None:
            raise RuntimeError("当前环境未安装可用的 MCP types 模块")

        return mcp_types.Implementation(
            name=self.client_config.client_name,
            version=self.client_config.client_version,
        )

    def _build_sampling_capabilities(self, sampling_callback: Any) -> Any | None:
        """构建 Sampling 能力声明。

        Args:
            sampling_callback: 当前宿主侧的 Sampling 回调。

        Returns:
            Any | None: Sampling 能力对象；未启用时返回 ``None``。
        """

        if mcp_types is None:
            return None
        if sampling_callback is None:
            return None

        context_capability = (
            mcp_types.SamplingContextCapability()
            if self.client_config.sampling_include_context_support
            else None
        )
        tools_capability = (
            mcp_types.SamplingToolsCapability()
            if self.client_config.sampling_tool_support
            else None
        )
        return mcp_types.SamplingCapability(
            context=context_capability,
            tools=tools_capability,
        )

    def _build_list_roots_callback(self) -> Any | None:
        """构建 Roots 列表回调。

        Returns:
            Any | None: 符合 MCP SDK 要求的回调；未启用时返回 ``None``。
        """

        if mcp_types is None:
            return None
        if not self.client_config.enable_roots or not self.client_config.roots:
            return None

        async def _list_roots(context: Any) -> Any:
            """返回当前客户端声明的 Roots 列表。

            Args:
                context: MCP 请求上下文。

            Returns:
                Any: MCP `ListRootsResult` 对象。
            """

            del context
            types_module = mcp_types
            if types_module is None:
                raise RuntimeError("当前环境未安装可用的 MCP types 模块")
            roots = [
                types_module.Root(uri=cast(Any, root.uri), name=root.name or None)
                for root in self.client_config.roots
            ]
            return types_module.ListRootsResult(roots=roots)

        return _list_roots

    async def _load_server_features(self) -> None:
        """根据服务端能力声明加载工具、Prompt 与 Resource。"""

        self.tools = await self._list_tools() if self.supports_tools() else []
        if not self.discover_extended_features:
            self.prompts = []
            self.resources = []
            self.resource_templates = []
            return

        prompt_task = self._list_prompts() if self.supports_prompts() else self._empty_feature_list()
        resource_task = self._list_resources() if self.supports_resources() else self._empty_feature_list()
        template_task = self._load_resource_templates()
        self.prompts, self.resources, self.resource_templates = await asyncio.gather(
            prompt_task,
            resource_task,
            template_task,
        )

    @staticmethod
    async def _empty_feature_list() -> list[Any]:
        """为并行能力发现返回空列表。"""

        return []

    async def _load_resource_templates(self) -> list[Any]:
        """加载可选 Resource Template，并兼容未实现该方法的服务端。"""

        if not self.supports_resources():
            return []
        # list_resource_templates 在 MCP spec 中是 OPTIONAL，部分 server（如 moegirl-wiki-mcp）
        # 仅实现 list_resources，遇到 METHOD_NOT_FOUND 时按空集合处理避免毁掉整个连接。
        try:
            return await self._list_resource_templates()
        except McpError as exc:
            if exc.error.code != mcp_types.METHOD_NOT_FOUND:
                raise
            console.print(
                f"[warning]⚠️ MCP 服务器 '{self.config.name}' 未实现 "
                f"list_resource_templates，按空集合处理[/warning]"
            )
            return []

    def supports_tools(self) -> bool:
        """判断服务端是否声明支持 Tools。

        Returns:
            bool: 是否支持 Tools。
        """

        return bool(self.server_capabilities is not None and getattr(self.server_capabilities, "tools", None) is not None)

    def supports_prompts(self) -> bool:
        """判断服务端是否声明支持 Prompts。

        Returns:
            bool: 是否支持 Prompts。
        """

        return bool(
            self.server_capabilities is not None and getattr(self.server_capabilities, "prompts", None) is not None
        )

    def supports_resources(self) -> bool:
        """判断服务端是否声明支持 Resources。

        Returns:
            bool: 是否支持 Resources。
        """

        return bool(
            self.server_capabilities is not None and getattr(self.server_capabilities, "resources", None) is not None
        )

    async def _list_tools(self) -> list[Any]:
        """分页加载服务端暴露的全部工具。

        Returns:
            list[Any]: MCP SDK 的原始工具对象列表。
        """

        if self.session is None:
            return []

        tools: list[Any] = []
        cursor: Optional[str] = None
        while True:
            result = await self.session.list_tools(cursor=cursor)
            tools.extend(list(getattr(result, "tools", []) or []))
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return tools

    async def _list_prompts(self) -> list[Any]:
        """分页加载服务端暴露的全部 Prompt。

        Returns:
            list[Any]: MCP SDK 的原始 Prompt 对象列表。
        """

        if self.session is None:
            return []

        prompts: list[Any] = []
        cursor: Optional[str] = None
        while True:
            result = await self.session.list_prompts(cursor=cursor)
            prompts.extend(list(getattr(result, "prompts", []) or []))
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return prompts

    async def _list_resources(self) -> list[Any]:
        """分页加载服务端暴露的全部 Resource。

        Returns:
            list[Any]: MCP SDK 的原始 Resource 对象列表。
        """

        if self.session is None:
            return []

        resources: list[Any] = []
        cursor: Optional[str] = None
        while True:
            result = await self.session.list_resources(cursor=cursor)
            resources.extend(list(getattr(result, "resources", []) or []))
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return resources

    async def _list_resource_templates(self) -> list[Any]:
        """分页加载服务端暴露的全部 Resource Template。

        Returns:
            list[Any]: MCP SDK 的原始 Resource Template 对象列表。
        """

        if self.session is None:
            return []

        resource_templates: list[Any] = []
        cursor: Optional[str] = None
        while True:
            result = await self.session.list_resource_templates(cursor=cursor)
            resource_templates.extend(list(getattr(result, "resourceTemplates", []) or []))
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return resource_templates

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        """调用 MCP 工具并返回统一执行结果。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            ToolExecutionResult: 统一执行结果。
        """

        if self.session is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"MCP 服务器 '{self.config.name}' 未连接",
                metadata={"server_name": self.config.name},
            )

        try:
            result = await self.session.call_tool(
                tool_name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=self.config.read_timeout_seconds),
            )
        except Exception as exc:
            # 部分异常（如流已关闭时的 ClosedResourceError）没有消息文本，需要用异常类名补全
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"MCP 工具 '{tool_name}' 执行失败: {_format_exception_message(exc)}",
                metadata={"server_name": self.config.name},
            )

        content_items = build_tool_content_items(list(getattr(result, "content", []) or []))
        text_parts = [item.text.strip() for item in content_items if item.content_type == "text" and item.text.strip()]
        structured_content = getattr(result, "structuredContent", None)
        is_error = bool(getattr(result, "isError", False))
        history_content = "\n".join(text_parts).strip()
        error_message = history_content if is_error else ""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=not is_error,
            content=history_content if not is_error else "",
            error_message=error_message,
            structured_content=structured_content,
            content_items=content_items,
            metadata={
                "server_name": self.config.name,
                "protocol_version": self.protocol_version,
                "session_id": self.session_id,
            },
        )

    async def get_prompt(
        self,
        prompt_name: str,
        arguments: Optional[dict[str, str]] = None,
    ) -> MCPPromptResult:
        """读取指定 MCP Prompt 的内容。

        Args:
            prompt_name: Prompt 名称。
            arguments: Prompt 参数字典。

        Returns:
            MCPPromptResult: 统一 Prompt 结果。
        """

        if self.session is None:
            raise RuntimeError(f"MCP 服务器 '{self.config.name}' 未连接")

        result = await self.session.get_prompt(prompt_name, arguments=arguments)
        return build_prompt_result(result, prompt_name=prompt_name, server_name=self.config.name)

    async def read_resource(self, uri: str) -> MCPResourceReadResult:
        """读取指定 MCP Resource 的内容。

        Args:
            uri: 资源 URI。

        Returns:
            MCPResourceReadResult: 统一资源读取结果。
        """

        if self.session is None:
            raise RuntimeError(f"MCP 服务器 '{self.config.name}' 未连接")

        result = await self.session.read_resource(uri)
        return build_resource_read_result(result, uri=uri, server_name=self.config.name)

    async def close(self) -> None:
        """关闭连接并释放资源。

        可在任意任务中调用：只向所有者任务发出关闭信号，并等待它在自身任务内退出全部上下文。
        所有者任务超过时限仍未退出时会被取消，取消引发的上下文回滚同样发生在所有者任务内。
        """

        lifecycle = self._lifecycle
        if lifecycle is None:
            return
        self._lifecycle = None

        lifecycle.close_requested.set()
        if not lifecycle.ready.done():
            # 尚未完成建连：无需等待初始化结束，直接取消所有者任务
            lifecycle.task.cancel()
        else:
            close_timeout_seconds = self._get_close_timeout_seconds()
            done, _pending = await asyncio.wait((lifecycle.task,), timeout=close_timeout_seconds)
            if not done:
                logger.warning(
                    f"MCP 服务器 '{self.config.name}' 的连接在 {close_timeout_seconds:g} 秒内未完成关闭，"
                    "正在取消其所有者任务"
                )
                lifecycle.task.cancel()
        await asyncio.wait((lifecycle.task,))

        if lifecycle.task.cancelled():
            return
        close_error = lifecycle.task.exception()
        if close_error is not None:
            logger.error(
                f"MCP 服务器 '{self.config.name}' 关闭连接失败: {_format_exception_message(close_error)}",
                exc_info=close_error,
            )

    def _get_close_timeout_seconds(self) -> float:
        """返回等待所有者任务正常退出的时限，超时后会取消该任务。

        Returns:
            float: 等待秒数。
        """

        if self.config.transport_type == "stdio":
            return MIN_CLOSE_TIMEOUT_SECONDS
        # HTTP 类传输关闭时可能需要向服务端发送会话终止请求，受 HTTP 超时约束
        return max(MIN_CLOSE_TIMEOUT_SECONDS, self.config.http_timeout_seconds + 5.0)

    def _reset_connection_state(self) -> None:
        """清空已结束连接的会话与能力状态。"""

        self.session = None
        self.server_capabilities = None
        self.tools = []
        self.prompts = []
        self.resources = []
        self.resource_templates = []
        self.protocol_version = ""
        self._http_client = None
        self._session_id_getter = None

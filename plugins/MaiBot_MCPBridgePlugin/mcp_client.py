"""
MCP 客户端封装模块
负责与 MCP 服务器建立连接、获取工具列表、执行工具调用

v1.7.0 稳定性优化:
- 断路器模式：连续失败 5 次后熔断，60 秒后试探恢复
- 熔断期间快速失败，避免等待超时
- 连接成功时自动重置断路器

v1.5.2 性能优化:
- 智能心跳间隔：根据服务器稳定性动态调整心跳频率
- 稳定服务器逐渐增加间隔（最高 3x），减少不必要的检测
- 断开的服务器使用较短间隔快速重连

v1.1.0 新增功能:
- 调用统计（次数、成功率、耗时）
- 心跳检测
- 自动重连
- 更好的错误处理

v1.2.0 新增功能:
- Resources 支持（资源读取）
- Prompts 支持（提示模板）
- 新增配置项: enable_resources, enable_prompts
"""

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

# 尝试导入 MaiBot 的 logger，如果失败则使用标准 logging
try:
    from src.common.logger import get_logger

    logger = get_logger("mcp_client")
except ImportError:
    # Fallback: 使用标准 logging
    logger = logging.getLogger("mcp_client")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


class TransportType(Enum):
    """MCP 传输类型"""

    STDIO = "stdio"  # 本地进程通信
    SSE = "sse"  # Server-Sent Events (旧版 HTTP)
    HTTP = "http"  # HTTP Streamable (新版，推荐)
    STREAMABLE_HTTP = "streamable_http"  # HTTP Streamable 的别名


@dataclass
class MCPToolInfo:
    """MCP 工具信息"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str


@dataclass
class MCPResourceInfo:
    """MCP 资源信息"""

    uri: str
    name: str
    description: str
    mime_type: Optional[str]
    server_name: str


@dataclass
class MCPPromptInfo:
    """MCP 提示模板信息"""

    name: str
    description: str
    arguments: List[Dict[str, Any]]  # [{name, description, required}]
    server_name: str


@dataclass
class MCPServerConfig:
    """MCP 服务器配置"""

    name: str
    enabled: bool = True
    transport: TransportType = TransportType.STDIO
    # stdio 配置
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    # http/sse 配置
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)  # v1.4.2: 鉴权头支持


@dataclass
class MCPCallResult:
    """MCP 工具调用结果"""

    success: bool
    content: Any
    error: Optional[str] = None
    duration_ms: float = 0.0  # 调用耗时（毫秒）
    circuit_broken: bool = False  # v1.7.0: 是否被断路器拦截


class CircuitState(Enum):
    """断路器状态"""

    CLOSED = "closed"  # 正常状态，允许请求
    OPEN = "open"  # 熔断状态，拒绝请求
    HALF_OPEN = "half_open"  # 半开状态，允许少量试探请求


@dataclass
class CircuitBreaker:
    """v1.7.0: 断路器 - 防止对故障服务器持续请求

    状态转换:
    - CLOSED -> OPEN: 连续失败次数达到阈值
    - OPEN -> HALF_OPEN: 熔断时间到期
    - HALF_OPEN -> CLOSED: 试探请求成功
    - HALF_OPEN -> OPEN: 试探请求失败
    """

    # 配置
    failure_threshold: int = 5  # 连续失败多少次后熔断
    recovery_timeout: float = 60.0  # 熔断后多久尝试恢复（秒）
    half_open_max_calls: int = 1  # 半开状态最多允许几次试探调用

    # 状态
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    half_open_calls: int = 0

    def can_execute(self) -> Tuple[bool, Optional[str]]:
        """检查是否允许执行请求

        Returns:
            (是否允许, 拒绝原因)
        """
        current_time = time.time()

        if self.state == CircuitState.CLOSED:
            return True, None

        if self.state == CircuitState.OPEN:
            # 检查是否到了恢复时间
            time_since_failure = current_time - self.last_failure_time
            if time_since_failure >= self.recovery_timeout:
                # 转换到半开状态
                self._transition_to(CircuitState.HALF_OPEN)
                return True, None
            else:
                remaining = self.recovery_timeout - time_since_failure
                return False, f"断路器熔断中，{remaining:.0f}秒后重试"

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态，检查是否还有试探配额
            if self.half_open_calls < self.half_open_max_calls:
                return True, None
            else:
                return False, "断路器半开状态，等待试探结果"

        return True, None

    def record_success(self) -> None:
        """记录成功调用"""
        self.success_count += 1

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态下成功，恢复到关闭状态
            self._transition_to(CircuitState.CLOSED)
            logger.info("断路器恢复正常（试探成功）")
        elif self.state == CircuitState.CLOSED:
            # 正常状态下成功，重置失败计数
            self.failure_count = 0

    def record_failure(self) -> None:
        """记录失败调用"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态下失败，重新熔断
            self._transition_to(CircuitState.OPEN)
            logger.warning("断路器重新熔断（试探失败）")
        elif self.state == CircuitState.CLOSED:
            # 检查是否达到熔断阈值
            if self.failure_count >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)
                logger.warning(f"断路器熔断（连续失败 {self.failure_count} 次）")

    def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()

        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.half_open_calls = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.half_open_calls = 0

        logger.debug(f"断路器状态: {old_state.value} -> {new_state.value}")

    def reset(self) -> None:
        """重置断路器"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_state_change = time.time()

    def get_status(self) -> Dict[str, Any]:
        """获取断路器状态"""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "time_since_last_failure": time.time() - self.last_failure_time if self.last_failure_time > 0 else None,
        }


@dataclass
class ToolCallStats:
    """工具调用统计"""

    tool_key: str
    total_calls: int = 0
    success_calls: int = 0
    failed_calls: int = 0
    total_duration_ms: float = 0.0
    last_call_time: Optional[float] = None
    last_error: Optional[str] = None

    @property
    def success_rate(self) -> float:
        """成功率（0-100）"""
        if self.total_calls == 0:
            return 0.0
        return (self.success_calls / self.total_calls) * 100

    @property
    def avg_duration_ms(self) -> float:
        """平均耗时（毫秒）"""
        if self.success_calls == 0:
            return 0.0
        return self.total_duration_ms / self.success_calls

    def record_call(self, success: bool, duration_ms: float, error: Optional[str] = None) -> None:
        """记录一次调用"""
        self.total_calls += 1
        self.last_call_time = time.time()
        if success:
            self.success_calls += 1
            self.total_duration_ms += duration_ms
        else:
            self.failed_calls += 1
            self.last_error = error

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "tool_key": self.tool_key,
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "failed_calls": self.failed_calls,
            "success_rate": round(self.success_rate, 2),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "last_call_time": self.last_call_time,
            "last_error": self.last_error,
        }


@dataclass
class ServerStats:
    """服务器统计"""

    server_name: str
    connect_count: int = 0  # 连接次数
    disconnect_count: int = 0  # 断开次数
    reconnect_count: int = 0  # 重连次数
    last_connect_time: Optional[float] = None
    last_disconnect_time: Optional[float] = None
    last_heartbeat_time: Optional[float] = None
    consecutive_failures: int = 0  # 连续失败次数

    def record_connect(self) -> None:
        self.connect_count += 1
        self.last_connect_time = time.time()
        self.consecutive_failures = 0

    def record_disconnect(self) -> None:
        self.disconnect_count += 1
        self.last_disconnect_time = time.time()

    def record_reconnect(self) -> None:
        self.reconnect_count += 1
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_heartbeat(self) -> None:
        self.last_heartbeat_time = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_name": self.server_name,
            "connect_count": self.connect_count,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "last_connect_time": self.last_connect_time,
            "last_disconnect_time": self.last_disconnect_time,
            "last_heartbeat_time": self.last_heartbeat_time,
            "consecutive_failures": self.consecutive_failures,
        }


class MCPClientSession:
    """MCP 客户端会话，管理与单个 MCP 服务器的连接"""

    def __init__(self, config: MCPServerConfig, call_timeout: float = 60.0):
        self.config = config
        self.call_timeout = call_timeout
        self._session = None
        self._read_stream = None
        self._write_stream = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._tools: List[MCPToolInfo] = []
        self._resources: List[MCPResourceInfo] = []  # v1.2.0: Resources 支持
        self._prompts: List[MCPPromptInfo] = []  # v1.2.0: Prompts 支持
        self._connected = False
        self._lock = asyncio.Lock()

        # 功能支持标记（服务器可能不支持某些功能）
        self._supports_resources: bool = False
        self._supports_prompts: bool = False

        # 统计信息
        self.stats = ServerStats(server_name=config.name)
        self._tool_stats: Dict[str, ToolCallStats] = {}

        # v1.7.0: 断路器
        self._circuit_breaker = CircuitBreaker()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> List[MCPToolInfo]:
        return self._tools.copy()

    @property
    def resources(self) -> List[MCPResourceInfo]:
        """v1.2.0: 获取资源列表"""
        return self._resources.copy()

    @property
    def prompts(self) -> List[MCPPromptInfo]:
        """v1.2.0: 获取提示模板列表"""
        return self._prompts.copy()

    @property
    def supports_resources(self) -> bool:
        """v1.2.0: 服务器是否支持 Resources"""
        return self._supports_resources

    @property
    def supports_prompts(self) -> bool:
        """v1.2.0: 服务器是否支持 Prompts"""
        return self._supports_prompts

    @property
    def server_name(self) -> str:
        return self.config.name

    def get_tool_stats(self, tool_name: str) -> Optional[ToolCallStats]:
        """获取工具统计"""
        return self._tool_stats.get(tool_name)

    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """v1.7.0: 获取断路器状态"""
        return self._circuit_breaker.get_status()

    def reset_circuit_breaker(self) -> None:
        """v1.7.0: 重置断路器"""
        self._circuit_breaker.reset()
        logger.info(f"[{self.server_name}] 断路器已重置")

    def get_all_tool_stats(self) -> Dict[str, ToolCallStats]:
        """获取所有工具统计"""
        return self._tool_stats.copy()

    async def connect(self) -> bool:
        """连接到 MCP 服务器"""
        async with self._lock:
            if self._connected:
                return True

            try:
                success = False
                if self.config.transport == TransportType.STDIO:
                    success = await self._connect_stdio()
                elif self.config.transport == TransportType.SSE:
                    success = await self._connect_sse()
                elif self.config.transport in (TransportType.HTTP, TransportType.STREAMABLE_HTTP):
                    success = await self._connect_http()
                else:
                    logger.error(f"[{self.server_name}] 不支持的传输类型: {self.config.transport}")
                    return False

                if success:
                    self.stats.record_connect()
                    # v1.7.0: 连接成功时重置断路器
                    self._circuit_breaker.reset()
                else:
                    self.stats.record_failure()
                return success

            except Exception as e:
                logger.error(f"[{self.server_name}] 连接失败: {e}")
                self._connected = False
                self.stats.record_failure()
                return False

    async def _connect_stdio(self) -> bool:
        """通过 stdio 连接 MCP 服务器"""
        try:
            try:
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
            except ImportError:
                logger.error(f"[{self.server_name}] 未安装 mcp 库，请运行: pip install mcp")
                return False

            server_params = StdioServerParameters(
                command=self.config.command, args=self.config.args, env=self.config.env if self.config.env else None
            )

            self._stdio_context = stdio_client(server_params)
            self._read_stream, self._write_stream = await self._stdio_context.__aenter__()

            self._session_context = ClientSession(self._read_stream, self._write_stream)
            self._session = await self._session_context.__aenter__()

            await self._session.initialize()
            await self._fetch_tools()

            self._connected = True
            logger.info(f"[{self.server_name}] stdio 连接成功，发现 {len(self._tools)} 个工具")
            return True

        except Exception as e:
            logger.error(f"[{self.server_name}] stdio 连接失败: {e}")
            await self._cleanup()
            return False

    async def _connect_sse(self) -> bool:
        """通过 SSE 连接 MCP 服务器"""
        try:
            try:
                from mcp import ClientSession
                from mcp.client.sse import sse_client
            except ImportError:
                logger.error(f"[{self.server_name}] 未安装 mcp 库，请运行: pip install mcp")
                return False

            if not self.config.url:
                logger.error(f"[{self.server_name}] SSE 传输需要配置 url")
                return False

            logger.debug(f"[{self.server_name}] 正在连接 SSE MCP 服务器: {self.config.url}")

            # v1.4.2: 支持 headers 鉴权
            sse_kwargs = {
                "url": self.config.url,
                "timeout": 60.0,
                "sse_read_timeout": 300.0,
            }
            if self.config.headers:
                sse_kwargs["headers"] = self.config.headers

            self._sse_context = sse_client(**sse_kwargs)
            self._read_stream, self._write_stream = await self._sse_context.__aenter__()

            self._session_context = ClientSession(self._read_stream, self._write_stream)
            self._session = await self._session_context.__aenter__()

            await self._session.initialize()
            await self._fetch_tools()

            self._connected = True
            logger.info(f"[{self.server_name}] SSE 连接成功，发现 {len(self._tools)} 个工具")
            return True

        except Exception as e:
            logger.error(f"[{self.server_name}] SSE 连接失败: {e}")
            import traceback

            logger.debug(f"[{self.server_name}] 详细错误: {traceback.format_exc()}")
            await self._cleanup()
            return False

    async def _connect_http(self) -> bool:
        """通过 HTTP Streamable 连接 MCP 服务器"""
        try:
            try:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamablehttp_client
            except ImportError:
                logger.error(f"[{self.server_name}] 未安装 mcp 库，请运行: pip install mcp")
                return False

            if not self.config.url:
                logger.error(f"[{self.server_name}] HTTP 传输需要配置 url")
                return False

            logger.debug(f"[{self.server_name}] 正在连接 HTTP MCP 服务器: {self.config.url}")

            # v1.4.2: 支持 headers 鉴权
            http_kwargs = {
                "url": self.config.url,
                "timeout": 60.0,
                "sse_read_timeout": 300.0,
            }
            if self.config.headers:
                http_kwargs["headers"] = self.config.headers

            self._http_context = streamablehttp_client(**http_kwargs)
            self._read_stream, self._write_stream, self._get_session_id = await self._http_context.__aenter__()

            self._session_context = ClientSession(self._read_stream, self._write_stream)
            self._session = await self._session_context.__aenter__()

            await self._session.initialize()
            await self._fetch_tools()

            self._connected = True
            logger.info(f"[{self.server_name}] HTTP 连接成功，发现 {len(self._tools)} 个工具")
            return True

        except Exception as e:
            logger.error(f"[{self.server_name}] HTTP 连接失败: {e}")
            import traceback

            logger.debug(f"[{self.server_name}] 详细错误: {traceback.format_exc()}")
            await self._cleanup()
            return False

    async def _fetch_tools(self) -> None:
        """获取 MCP 服务器的工具列表"""
        if not self._session:
            return

        try:
            result = await self._session.list_tools()
            self._tools = []

            for tool in result.tools:
                tool_info = MCPToolInfo(
                    name=tool.name,
                    description=tool.description or f"MCP tool: {tool.name}",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    server_name=self.server_name,
                )
                self._tools.append(tool_info)
                # 初始化工具统计
                if tool.name not in self._tool_stats:
                    self._tool_stats[tool.name] = ToolCallStats(tool_key=tool.name)
                logger.debug(f"[{self.server_name}] 发现工具: {tool.name}")

        except Exception as e:
            logger.error(f"[{self.server_name}] 获取工具列表失败: {e}")
            self._tools = []

    async def fetch_resources(self) -> bool:
        """v1.2.0: 获取 MCP 服务器的资源列表

        Returns:
            bool: 是否成功获取（服务器不支持时返回 False）
        """
        if not self._session:
            return False

        try:
            result = await asyncio.wait_for(self._session.list_resources(), timeout=self.call_timeout)
            self._resources = []

            for resource in result.resources:
                resource_info = MCPResourceInfo(
                    uri=str(resource.uri),
                    name=resource.name or str(resource.uri),
                    description=resource.description or "",
                    mime_type=resource.mimeType if hasattr(resource, "mimeType") else None,
                    server_name=self.server_name,
                )
                self._resources.append(resource_info)
                logger.debug(f"[{self.server_name}] 发现资源: {resource_info.uri}")

            self._supports_resources = True
            logger.info(f"[{self.server_name}] 获取到 {len(self._resources)} 个资源")
            return True

        except Exception as e:
            # 服务器可能不支持 resources，这不是错误
            error_str = str(e).lower()
            if "not supported" in error_str or "not implemented" in error_str or "method not found" in error_str:
                logger.debug(f"[{self.server_name}] 服务器不支持 Resources 功能")
            else:
                logger.warning(f"[{self.server_name}] 获取资源列表失败: {e}")
            self._supports_resources = False
            self._resources = []
            return False

    async def fetch_prompts(self) -> bool:
        """v1.2.0: 获取 MCP 服务器的提示模板列表

        Returns:
            bool: 是否成功获取（服务器不支持时返回 False）
        """
        if not self._session:
            return False

        try:
            result = await asyncio.wait_for(self._session.list_prompts(), timeout=self.call_timeout)
            self._prompts = []

            for prompt in result.prompts:
                # 解析参数
                arguments = []
                if hasattr(prompt, "arguments") and prompt.arguments:
                    for arg in prompt.arguments:
                        arguments.append(
                            {
                                "name": arg.name,
                                "description": arg.description or "",
                                "required": arg.required if hasattr(arg, "required") else False,
                            }
                        )

                prompt_info = MCPPromptInfo(
                    name=prompt.name,
                    description=prompt.description or f"MCP prompt: {prompt.name}",
                    arguments=arguments,
                    server_name=self.server_name,
                )
                self._prompts.append(prompt_info)
                logger.debug(f"[{self.server_name}] 发现提示模板: {prompt.name}")

            self._supports_prompts = True
            logger.info(f"[{self.server_name}] 获取到 {len(self._prompts)} 个提示模板")
            return True

        except Exception as e:
            # 服务器可能不支持 prompts，这不是错误
            error_str = str(e).lower()
            if "not supported" in error_str or "not implemented" in error_str or "method not found" in error_str:
                logger.debug(f"[{self.server_name}] 服务器不支持 Prompts 功能")
            else:
                logger.warning(f"[{self.server_name}] 获取提示模板列表失败: {e}")
            self._supports_prompts = False
            self._prompts = []
            return False

    async def read_resource(self, uri: str) -> MCPCallResult:
        """v1.2.0: 读取指定资源的内容

        Args:
            uri: 资源 URI

        Returns:
            MCPCallResult: 包含资源内容的结果
        """
        start_time = time.time()

        if not self._connected or not self._session:
            return MCPCallResult(success=False, content=None, error=f"服务器 {self.server_name} 未连接")

        if not self._supports_resources:
            return MCPCallResult(success=False, content=None, error=f"服务器 {self.server_name} 不支持 Resources 功能")

        try:
            result = await asyncio.wait_for(self._session.read_resource(uri), timeout=self.call_timeout)

            duration_ms = (time.time() - start_time) * 1000

            # 处理返回内容
            content_parts = []
            for content in result.contents:
                if hasattr(content, "text"):
                    content_parts.append(content.text)
                elif hasattr(content, "blob"):
                    # 二进制数据，返回 base64 或提示
                    import base64

                    blob_data = content.blob
                    if len(blob_data) < 10000:  # 小于 10KB 返回 base64
                        content_parts.append(f"[base64]{base64.b64encode(blob_data).decode()}")
                    else:
                        content_parts.append(f"[二进制数据: {len(blob_data)} bytes]")
                else:
                    content_parts.append(str(content))

            return MCPCallResult(
                success=True, content="\n".join(content_parts) if content_parts else "", duration_ms=duration_ms
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            return MCPCallResult(
                success=False, content=None, error=f"读取资源超时（{self.call_timeout}秒）", duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"[{self.server_name}] 读取资源 {uri} 失败: {e}")
            return MCPCallResult(success=False, content=None, error=str(e), duration_ms=duration_ms)

    async def get_prompt(self, name: str, arguments: Optional[Dict[str, str]] = None) -> MCPCallResult:
        """v1.2.0: 获取提示模板的内容

        Args:
            name: 提示模板名称
            arguments: 模板参数

        Returns:
            MCPCallResult: 包含提示内容的结果
        """
        start_time = time.time()

        if not self._connected or not self._session:
            return MCPCallResult(success=False, content=None, error=f"服务器 {self.server_name} 未连接")

        if not self._supports_prompts:
            return MCPCallResult(success=False, content=None, error=f"服务器 {self.server_name} 不支持 Prompts 功能")

        try:
            result = await asyncio.wait_for(
                self._session.get_prompt(name, arguments=arguments or {}), timeout=self.call_timeout
            )

            duration_ms = (time.time() - start_time) * 1000

            # 处理返回的消息
            messages = []
            for msg in result.messages:
                role = msg.role if hasattr(msg, "role") else "unknown"
                content_text = ""
                if hasattr(msg, "content"):
                    if hasattr(msg.content, "text"):
                        content_text = msg.content.text
                    elif isinstance(msg.content, str):
                        content_text = msg.content
                    else:
                        content_text = str(msg.content)
                messages.append(f"[{role}]: {content_text}")

            return MCPCallResult(
                success=True, content="\n\n".join(messages) if messages else "", duration_ms=duration_ms
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            return MCPCallResult(
                success=False, content=None, error=f"获取提示模板超时（{self.call_timeout}秒）", duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"[{self.server_name}] 获取提示模板 {name} 失败: {e}")
            return MCPCallResult(success=False, content=None, error=str(e), duration_ms=duration_ms)

    async def check_health(self) -> bool:
        """检查连接健康状态（心跳检测）

        通过调用 list_tools 来验证连接是否正常
        """
        if not self._connected or not self._session:
            return False

        try:
            # 使用 list_tools 作为心跳检测
            await asyncio.wait_for(self._session.list_tools(), timeout=10.0)
            self.stats.record_heartbeat()
            return True
        except Exception as e:
            logger.warning(f"[{self.server_name}] 心跳检测失败: {e}")
            # 标记为断开
            self._connected = False
            self.stats.record_disconnect()
            return False

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MCPCallResult:
        """调用 MCP 工具"""
        start_time = time.time()

        # v1.7.0: 断路器检查
        can_execute, reject_reason = self._circuit_breaker.can_execute()
        if not can_execute:
            return MCPCallResult(success=False, content=None, error=f"⚡ {reject_reason}", circuit_broken=True)

        # 半开状态下增加试探计数
        if self._circuit_breaker.state == CircuitState.HALF_OPEN:
            self._circuit_breaker.half_open_calls += 1

        if not self._connected or not self._session:
            error_msg = f"服务器 {self.server_name} 未连接"
            # 记录失败
            if tool_name in self._tool_stats:
                self._tool_stats[tool_name].record_call(False, 0, error_msg)
            self._circuit_breaker.record_failure()
            return MCPCallResult(success=False, content=None, error=error_msg)

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments=arguments), timeout=self.call_timeout
            )

            duration_ms = (time.time() - start_time) * 1000

            # 处理返回内容
            content_parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    content_parts.append(content.text)
                elif hasattr(content, "data"):
                    content_parts.append(f"[二进制数据: {len(content.data)} bytes]")
                else:
                    content_parts.append(str(content))

            # 记录成功
            if tool_name in self._tool_stats:
                self._tool_stats[tool_name].record_call(True, duration_ms)

            # v1.7.0: 断路器记录成功
            self._circuit_breaker.record_success()

            return MCPCallResult(
                success=True,
                content="\n".join(content_parts) if content_parts else "执行成功（无返回内容）",
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = f"工具调用超时（{self.call_timeout}秒）"
            if tool_name in self._tool_stats:
                self._tool_stats[tool_name].record_call(False, duration_ms, error_msg)
            # v1.7.0: 断路器记录失败
            self._circuit_breaker.record_failure()
            return MCPCallResult(success=False, content=None, error=error_msg, duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(e)
            logger.error(f"[{self.server_name}] 调用工具 {tool_name} 失败: {e}")
            if tool_name in self._tool_stats:
                self._tool_stats[tool_name].record_call(False, duration_ms, error_msg)
            # v1.7.0: 断路器记录失败
            self._circuit_breaker.record_failure()
            # 检查是否是连接问题
            if "connection" in error_msg.lower() or "closed" in error_msg.lower():
                self._connected = False
                self.stats.record_disconnect()
            return MCPCallResult(success=False, content=None, error=error_msg, duration_ms=duration_ms)

    async def disconnect(self) -> None:
        """断开连接"""
        async with self._lock:
            if self._connected:
                self.stats.record_disconnect()
            await self._cleanup()

    async def _cleanup(self) -> None:
        """清理资源"""
        self._connected = False
        self._tools = []
        self._resources = []  # v1.2.0
        self._prompts = []  # v1.2.0
        self._supports_resources = False  # v1.2.0
        self._supports_prompts = False  # v1.2.0

        try:
            if hasattr(self, "_session_context") and self._session_context:
                await self._session_context.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"[{self.server_name}] 关闭会话时出错: {e}")

        try:
            if hasattr(self, "_stdio_context") and self._stdio_context:
                await self._stdio_context.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"[{self.server_name}] 关闭 stdio 连接时出错: {e}")

        try:
            if hasattr(self, "_http_context") and self._http_context:
                await self._http_context.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"[{self.server_name}] 关闭 HTTP 连接时出错: {e}")

        try:
            if hasattr(self, "_sse_context") and self._sse_context:
                await self._sse_context.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"[{self.server_name}] 关闭 SSE 连接时出错: {e}")

        self._session = None
        self._session_context = None
        self._stdio_context = None
        self._http_context = None
        self._sse_context = None
        self._read_stream = None
        self._write_stream = None

        logger.debug(f"[{self.server_name}] 连接已关闭")


class MCPClientManager:
    """MCP 客户端管理器，管理多个 MCP 服务器连接

    功能:
    - 管理多个 MCP 服务器连接
    - 心跳检测和自动重连
    - 调用统计
    """

    _instance: Optional["MCPClientManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._clients: Dict[str, MCPClientSession] = {}
        self._all_tools: Dict[str, Tuple[MCPToolInfo, MCPClientSession]] = {}
        self._all_resources: Dict[str, Tuple[MCPResourceInfo, MCPClientSession]] = {}  # v1.2.0
        self._all_prompts: Dict[str, Tuple[MCPPromptInfo, MCPClientSession]] = {}  # v1.2.0
        self._settings: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # 心跳检测任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_running = False

        # 状态变化回调
        self._on_status_change: Optional[callable] = None

        # 全局统计
        self._global_stats = {
            "total_tool_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "start_time": time.time(),
        }

    def configure(self, settings: Dict[str, Any]) -> None:
        """配置管理器"""
        self._settings = settings

    def set_status_change_callback(self, callback: callable) -> None:
        """设置状态变化回调函数"""
        self._on_status_change = callback

    def _notify_status_change(self) -> None:
        """通知状态变化"""
        if self._on_status_change:
            try:
                self._on_status_change()
            except Exception as e:
                logger.debug(f"状态变化回调出错: {e}")

    @property
    def all_tools(self) -> Dict[str, Tuple[MCPToolInfo, MCPClientSession]]:
        """获取所有已注册的工具"""
        return self._all_tools.copy()

    @property
    def all_resources(self) -> Dict[str, Tuple[MCPResourceInfo, MCPClientSession]]:
        """v1.2.0: 获取所有已注册的资源"""
        return self._all_resources.copy()

    @property
    def all_prompts(self) -> Dict[str, Tuple[MCPPromptInfo, MCPClientSession]]:
        """v1.2.0: 获取所有已注册的提示模板"""
        return self._all_prompts.copy()

    @property
    def connected_servers(self) -> List[str]:
        """获取已连接的服务器列表"""
        return [name for name, client in self._clients.items() if client.is_connected]

    @property
    def disconnected_servers(self) -> List[str]:
        """获取已断开的服务器列表"""
        return [name for name, client in self._clients.items() if not client.is_connected and client.config.enabled]

    async def add_server(self, config: MCPServerConfig) -> bool:
        """添加并连接 MCP 服务器"""
        async with self._lock:
            if config.name in self._clients:
                logger.warning(f"服务器 {config.name} 已存在")
                return False

            call_timeout = self._settings.get("call_timeout", 60.0)
            client = MCPClientSession(config, call_timeout)
            self._clients[config.name] = client

            if not config.enabled:
                logger.info(f"服务器 {config.name} 已添加但未启用")
                return True

            # 尝试连接
            retry_attempts = self._settings.get("retry_attempts", 3)
            retry_interval = self._settings.get("retry_interval", 5.0)

            for attempt in range(1, retry_attempts + 1):
                if await client.connect():
                    self._register_tools(client)
                    return True

                if attempt < retry_attempts:
                    logger.warning(
                        f"服务器 {config.name} 连接失败，{retry_interval}秒后重试 ({attempt}/{retry_attempts})"
                    )
                    await asyncio.sleep(retry_interval)

            logger.error(f"服务器 {config.name} 连接失败，已达最大重试次数 ({retry_attempts})")
            # 连接失败，但保留在 _clients 中以便后续重连
            return False

    def _register_tools(self, client: MCPClientSession) -> None:
        """注册客户端的工具"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")

        for tool in client.tools:
            if tool.name.startswith(f"{tool_prefix}_{client.server_name}_"):
                tool_key = tool.name
            else:
                tool_key = f"{tool_prefix}_{client.server_name}_{tool.name}"
            self._all_tools[tool_key] = (tool, client)
            logger.debug(f"注册 MCP 工具: {tool_key}")

    def _unregister_tools(self, server_name: str) -> List[str]:
        """注销服务器的工具，返回被注销的工具键列表"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")
        prefix = f"{tool_prefix}_{server_name}_"

        keys_to_remove = [k for k in self._all_tools.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._all_tools[key]
            logger.debug(f"注销 MCP 工具: {key}")
        return keys_to_remove

    def _register_resources(self, client: MCPClientSession) -> None:
        """v1.2.0: 注册客户端的资源"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")

        for resource in client.resources:
            # 资源键格式: mcp_{server}_{uri_safe_name}
            # 将 URI 转换为安全的键名
            safe_uri = resource.uri.replace("://", "_").replace("/", "_").replace(".", "_")
            resource_key = f"{tool_prefix}_{client.server_name}_res_{safe_uri}"
            self._all_resources[resource_key] = (resource, client)
            logger.debug(f"注册 MCP 资源: {resource_key}")

    def _unregister_resources(self, server_name: str) -> List[str]:
        """v1.2.0: 注销服务器的资源"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")
        prefix = f"{tool_prefix}_{server_name}_res_"

        keys_to_remove = [k for k in self._all_resources.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._all_resources[key]
            logger.debug(f"注销 MCP 资源: {key}")
        return keys_to_remove

    def _register_prompts(self, client: MCPClientSession) -> None:
        """v1.2.0: 注册客户端的提示模板"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")

        for prompt in client.prompts:
            prompt_key = f"{tool_prefix}_{client.server_name}_prompt_{prompt.name}"
            self._all_prompts[prompt_key] = (prompt, client)
            logger.debug(f"注册 MCP 提示模板: {prompt_key}")

    def _unregister_prompts(self, server_name: str) -> List[str]:
        """v1.2.0: 注销服务器的提示模板"""
        tool_prefix = self._settings.get("tool_prefix", "mcp")
        prefix = f"{tool_prefix}_{server_name}_prompt_"

        keys_to_remove = [k for k in self._all_prompts.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._all_prompts[key]
            logger.debug(f"注销 MCP 提示模板: {key}")
        return keys_to_remove

    async def remove_server(self, server_name: str) -> bool:
        """移除 MCP 服务器"""
        async with self._lock:
            if server_name not in self._clients:
                return False

            client = self._clients[server_name]
            await client.disconnect()
            self._unregister_tools(server_name)
            self._unregister_resources(server_name)  # v1.2.0
            self._unregister_prompts(server_name)  # v1.2.0
            del self._clients[server_name]

            logger.info(f"服务器 {server_name} 已移除")
            return True

    async def reconnect_server(self, server_name: str) -> bool:
        """重新连接服务器"""
        if server_name not in self._clients:
            return False

        client = self._clients[server_name]

        async with self._lock:
            self._unregister_tools(server_name)
            self._unregister_resources(server_name)  # v1.2.0
            self._unregister_prompts(server_name)  # v1.2.0
            await client.disconnect()

        # 尝试重连
        retry_attempts = self._settings.get("retry_attempts", 3)
        retry_interval = self._settings.get("retry_interval", 5.0)

        for attempt in range(1, retry_attempts + 1):
            if await client.connect():
                async with self._lock:
                    self._register_tools(client)
                    # v1.2.0: 重连后也尝试获取 resources 和 prompts
                    if self._settings.get("enable_resources", False):
                        await client.fetch_resources()
                        self._register_resources(client)
                    if self._settings.get("enable_prompts", False):
                        await client.fetch_prompts()
                        self._register_prompts(client)
                client.stats.record_reconnect()
                logger.info(f"服务器 {server_name} 重连成功")
                return True

            if attempt < retry_attempts:
                logger.warning(f"服务器 {server_name} 重连失败，{retry_interval}秒后重试 ({attempt}/{retry_attempts})")
                await asyncio.sleep(retry_interval)

        logger.error(f"服务器 {server_name} 重连失败")
        return False

    async def call_tool(self, tool_key: str, arguments: Dict[str, Any]) -> MCPCallResult:
        """调用 MCP 工具"""
        if tool_key not in self._all_tools:
            return MCPCallResult(success=False, content=None, error=f"工具 {tool_key} 不存在")

        tool_info, client = self._all_tools[tool_key]

        # 更新全局统计
        self._global_stats["total_tool_calls"] += 1

        result = await client.call_tool(tool_info.name, arguments)

        if result.success:
            self._global_stats["successful_calls"] += 1
        else:
            self._global_stats["failed_calls"] += 1

        return result

    async def fetch_resources_for_server(self, server_name: str) -> bool:
        """v1.2.0: 获取指定服务器的资源列表"""
        if server_name not in self._clients:
            return False

        client = self._clients[server_name]
        if not client.is_connected:
            return False

        success = await client.fetch_resources()
        if success:
            async with self._lock:
                self._register_resources(client)
        return success

    async def fetch_prompts_for_server(self, server_name: str) -> bool:
        """v1.2.0: 获取指定服务器的提示模板列表"""
        if server_name not in self._clients:
            return False

        client = self._clients[server_name]
        if not client.is_connected:
            return False

        success = await client.fetch_prompts()
        if success:
            async with self._lock:
                self._register_prompts(client)
        return success

    async def read_resource(self, uri: str, server_name: Optional[str] = None) -> MCPCallResult:
        """v1.2.0: 读取资源内容

        Args:
            uri: 资源 URI
            server_name: 指定服务器名称（可选，不指定则自动查找）
        """
        # 如果指定了服务器
        if server_name:
            if server_name not in self._clients:
                return MCPCallResult(success=False, content=None, error=f"服务器 {server_name} 不存在")
            client = self._clients[server_name]
            return await client.read_resource(uri)

        # 自动查找拥有该资源的服务器
        for resource_key, (resource_info, client) in self._all_resources.items():
            if resource_info.uri == uri:
                return await client.read_resource(uri)

        # 尝试在所有支持 resources 的服务器上查找
        for client in self._clients.values():
            if client.is_connected and client.supports_resources:
                result = await client.read_resource(uri)
                if result.success:
                    return result

        return MCPCallResult(success=False, content=None, error=f"未找到资源: {uri}")

    async def get_prompt(
        self, name: str, arguments: Optional[Dict[str, str]] = None, server_name: Optional[str] = None
    ) -> MCPCallResult:
        """v1.2.0: 获取提示模板内容

        Args:
            name: 提示模板名称
            arguments: 模板参数
            server_name: 指定服务器名称（可选）
        """
        # 如果指定了服务器
        if server_name:
            if server_name not in self._clients:
                return MCPCallResult(success=False, content=None, error=f"服务器 {server_name} 不存在")
            client = self._clients[server_name]
            return await client.get_prompt(name, arguments)

        # 自动查找拥有该提示模板的服务器
        for prompt_key, (prompt_info, client) in self._all_prompts.items():
            if prompt_info.name == name:
                return await client.get_prompt(name, arguments)

        return MCPCallResult(success=False, content=None, error=f"未找到提示模板: {name}")

    # ==================== 心跳检测 ====================

    async def start_heartbeat(self) -> None:
        """启动心跳检测任务"""
        if self._heartbeat_running:
            logger.warning("心跳检测任务已在运行")
            return

        heartbeat_enabled = self._settings.get("heartbeat_enabled", True)
        if not heartbeat_enabled:
            logger.info("心跳检测已禁用")
            return

        self._heartbeat_running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("心跳检测任务已启动")

    async def stop_heartbeat(self) -> None:
        """停止心跳检测任务"""
        self._heartbeat_running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info("心跳检测任务已停止")

    async def _heartbeat_loop(self) -> None:
        """心跳检测循环（v1.5.2: 智能心跳间隔）"""
        base_interval = self._settings.get("heartbeat_interval", 60.0)
        auto_reconnect = self._settings.get("auto_reconnect", True)
        max_reconnect_attempts = self._settings.get("max_reconnect_attempts", 3)

        # v1.5.2: 智能心跳配置
        adaptive_enabled = self._settings.get("heartbeat_adaptive", True)
        max_multiplier = self._settings.get("heartbeat_max_multiplier", 3.0)

        # 每个服务器独立的心跳间隔（根据稳定性动态调整）
        server_intervals: Dict[str, float] = {}
        min_interval = max(base_interval * 0.5, 30.0)  # 最小间隔
        max_interval = base_interval * max_multiplier  # 最大间隔

        mode_str = "智能" if adaptive_enabled else "固定"
        logger.info(f"心跳检测循环启动，{mode_str}模式，基准间隔: {base_interval}秒")

        while self._heartbeat_running:
            try:
                # 使用最小的服务器间隔作为循环间隔
                current_interval = min(server_intervals.values()) if server_intervals else base_interval
                current_interval = max(current_interval, min_interval)

                await asyncio.sleep(current_interval)

                if not self._heartbeat_running:
                    break

                current_time = time.time()

                # 检查所有已启用的服务器
                for server_name, client in list(self._clients.items()):
                    if not client.config.enabled:
                        continue

                    # 初始化服务器间隔
                    if server_name not in server_intervals:
                        server_intervals[server_name] = base_interval

                    # 检查是否到达该服务器的心跳时间
                    last_heartbeat = client.stats.last_heartbeat_time or 0
                    if current_time - last_heartbeat < server_intervals[server_name] * 0.9:
                        continue  # 还没到心跳时间

                    if client.is_connected:
                        # 检查健康状态
                        healthy = await client.check_health()
                        if healthy:
                            # v1.5.2: 智能心跳 - 稳定服务器逐渐增加间隔
                            if adaptive_enabled and client.stats.consecutive_failures == 0:
                                new_interval = min(server_intervals[server_name] * 1.2, max_interval)
                                if new_interval != server_intervals[server_name]:
                                    server_intervals[server_name] = new_interval
                                    logger.debug(f"[{server_name}] 稳定，心跳间隔调整为 {new_interval:.0f}s")
                        else:
                            logger.warning(f"[{server_name}] 心跳检测失败，连接可能已断开")
                            # 失败后重置为基准间隔
                            if adaptive_enabled:
                                server_intervals[server_name] = base_interval
                            self._notify_status_change()
                            if auto_reconnect:
                                await self._try_reconnect(server_name, max_reconnect_attempts)
                    else:
                        # 服务器未连接，尝试重连
                        if adaptive_enabled:
                            # 智能心跳：断开的服务器使用较短间隔
                            server_intervals[server_name] = min_interval
                        if auto_reconnect and client.stats.consecutive_failures < max_reconnect_attempts:
                            logger.info(f"[{server_name}] 检测到断开，尝试重连...")
                            await self._try_reconnect(server_name, max_reconnect_attempts)
                        elif client.stats.consecutive_failures >= max_reconnect_attempts:
                            if adaptive_enabled:
                                # 达到最大重连次数，降低检测频率
                                server_intervals[server_name] = max_interval
                            logger.debug(f"[{server_name}] 已达最大重连次数，降低检测频率")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳检测循环出错: {e}")
                await asyncio.sleep(5)

    async def _try_reconnect(self, server_name: str, max_attempts: int) -> bool:
        """尝试重连服务器"""
        client = self._clients.get(server_name)
        if not client:
            return False

        if client.stats.consecutive_failures >= max_attempts:
            logger.warning(f"[{server_name}] 连续失败次数已达上限 ({max_attempts})，暂停重连")
            return False

        logger.info(f"[{server_name}] 尝试重连 (失败次数: {client.stats.consecutive_failures}/{max_attempts})")

        success = await self.reconnect_server(server_name)
        if not success:
            client.stats.record_failure()

        self._notify_status_change()  # 重连后更新状态
        return success

    # ==================== 统计和状态 ====================

    def get_tool_stats(self, tool_key: str) -> Optional[Dict[str, Any]]:
        """获取指定工具的统计信息"""
        if tool_key not in self._all_tools:
            return None

        tool_info, client = self._all_tools[tool_key]
        stats = client.get_tool_stats(tool_info.name)
        return stats.to_dict() if stats else None

    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有统计信息"""
        server_stats = {}
        tool_stats = {}

        for server_name, client in self._clients.items():
            server_stats[server_name] = client.stats.to_dict()
            for tool_name, stats in client.get_all_tool_stats().items():
                full_key = f"{self._settings.get('tool_prefix', 'mcp')}_{server_name}_{tool_name}"
                tool_stats[full_key] = stats.to_dict()

        uptime = time.time() - self._global_stats["start_time"]

        return {
            "global": {
                **self._global_stats,
                "uptime_seconds": round(uptime, 2),
                "calls_per_minute": round(self._global_stats["total_tool_calls"] / (uptime / 60), 2)
                if uptime > 0
                else 0,
            },
            "servers": server_stats,
            "tools": tool_stats,
        }

    async def shutdown(self) -> None:
        """关闭所有连接"""
        # 停止心跳检测
        await self.stop_heartbeat()

        async with self._lock:
            for client in self._clients.values():
                await client.disconnect()
            self._clients.clear()
            self._all_tools.clear()
            self._all_resources.clear()  # v1.2.0
            self._all_prompts.clear()  # v1.2.0
            logger.info("MCP 客户端管理器已关闭")

    def get_status(self) -> Dict[str, Any]:
        """获取状态信息"""
        return {
            "total_servers": len(self._clients),
            "connected_servers": len(self.connected_servers),
            "disconnected_servers": len(self.disconnected_servers),
            "total_tools": len(self._all_tools),
            "total_resources": len(self._all_resources),  # v1.2.0
            "total_prompts": len(self._all_prompts),  # v1.2.0
            "heartbeat_running": self._heartbeat_running,
            "servers": {
                name: {
                    "connected": client.is_connected,
                    "enabled": client.config.enabled,
                    "tools_count": len(client.tools),
                    "resources_count": len(client.resources),  # v1.2.0
                    "prompts_count": len(client.prompts),  # v1.2.0
                    "supports_resources": client.supports_resources,  # v1.2.0
                    "supports_prompts": client.supports_prompts,  # v1.2.0
                    "transport": client.config.transport.value,
                    "consecutive_failures": client.stats.consecutive_failures,
                    "circuit_breaker": client.get_circuit_breaker_status(),  # v1.7.0
                }
                for name, client in self._clients.items()
            },
            "global_stats": self._global_stats,
        }


# 全局单例
mcp_manager = MCPClientManager()

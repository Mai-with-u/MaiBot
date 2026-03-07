"""RPC Envelope 消息模型

定义 Host 与 Runner 之间所有 RPC 消息的统一信封格式。
使用 Pydantic 进行 schema 定义与校验。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

import time


# ─── 协议常量 ──────────────────────────────────────────────────────

PROTOCOL_VERSION = "1.0"

# 支持的 SDK 版本范围（Host 在握手时校验）
MIN_SDK_VERSION = "1.0.0"
MAX_SDK_VERSION = "1.99.99"


# ─── 消息类型 ──────────────────────────────────────────────────────

class MessageType(str, Enum):
    """RPC 消息类型"""
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"


# ─── 请求 ID 生成器 ───────────────────────────────────────────────

class RequestIdGenerator:
    """单调递增 int64 请求 ID 生成器（线程安全由调用方保证或使用 asyncio）"""

    def __init__(self, start: int = 1):
        self._counter = start

    def next(self) -> int:
        current = self._counter
        self._counter += 1
        return current


# ─── Envelope 模型 ─────────────────────────────────────────────────

class Envelope(BaseModel):
    """RPC 统一信封

    所有 Host <-> Runner 消息均封装为此格式。
    序列化流程：Envelope -> .model_dump() -> MsgPack encode
    反序列化流程：MsgPack decode -> Envelope.model_validate(data)
    """

    protocol_version: str = Field(default=PROTOCOL_VERSION, description="协议版本")
    request_id: int = Field(description="单调递增请求 ID")
    message_type: MessageType = Field(description="消息类型")
    method: str = Field(default="", description="RPC 方法名")
    plugin_id: str = Field(default="", description="目标插件 ID")
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000), description="发送时间戳(ms)")
    timeout_ms: int = Field(default=30000, description="相对超时(ms)")
    generation: int = Field(default=0, description="Runner generation 编号")
    payload: dict[str, Any] = Field(default_factory=dict, description="业务数据")
    error: dict[str, Any] | None = Field(default=None, description="错误信息(仅 response)")

    def is_request(self) -> bool:
        return self.message_type == MessageType.REQUEST

    def is_response(self) -> bool:
        return self.message_type == MessageType.RESPONSE

    def is_event(self) -> bool:
        return self.message_type == MessageType.EVENT

    def make_response(self, payload: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> "Envelope":
        """基于当前请求创建对应的响应信封"""
        return Envelope(
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            message_type=MessageType.RESPONSE,
            method=self.method,
            plugin_id=self.plugin_id,
            generation=self.generation,
            payload=payload or {},
            error=error,
        )

    def make_error_response(self, code: str, message: str = "", details: dict | None = None) -> "Envelope":
        """基于当前请求创建错误响应"""
        return self.make_response(
            error={
                "code": code,
                "message": message,
                "details": details or {},
            }
        )


# ─── 握手消息 ──────────────────────────────────────────────────────

class HelloPayload(BaseModel):
    """runner.hello 握手请求 payload"""
    runner_id: str = Field(description="Runner 进程唯一标识")
    sdk_version: str = Field(description="SDK 版本号")
    session_token: str = Field(description="一次性会话令牌")


class HelloResponsePayload(BaseModel):
    """runner.hello 握手响应 payload"""
    accepted: bool = Field(description="是否接受连接")
    host_version: str = Field(default="", description="Host 版本号")
    assigned_generation: int = Field(default=0, description="分配的 generation 编号")
    reason: str = Field(default="", description="拒绝原因(若 accepted=False)")


# ─── 组件注册消息 ──────────────────────────────────────────────────

class ComponentDeclaration(BaseModel):
    """单个组件声明"""
    name: str = Field(description="组件名称")
    component_type: str = Field(description="组件类型: action/command/tool/event_handler")
    plugin_id: str = Field(description="所属插件 ID")
    metadata: dict[str, Any] = Field(default_factory=dict, description="组件元数据")


class RegisterComponentsPayload(BaseModel):
    """plugin.register_components 请求 payload"""
    plugin_id: str = Field(description="插件 ID")
    plugin_version: str = Field(default="1.0.0", description="插件版本")
    components: list[ComponentDeclaration] = Field(default_factory=list, description="组件列表")
    capabilities_required: list[str] = Field(default_factory=list, description="所需能力列表")


# ─── 调用消息 ──────────────────────────────────────────────────────

class InvokePayload(BaseModel):
    """plugin.invoke_* 请求 payload"""
    component_name: str = Field(description="要调用的组件名称")
    args: dict[str, Any] = Field(default_factory=dict, description="调用参数")


class InvokeResultPayload(BaseModel):
    """plugin.invoke_* 响应 payload"""
    success: bool = Field(description="是否成功")
    result: Any = Field(default=None, description="返回值")


# ─── 能力调用消息 ──────────────────────────────────────────────────

class CapabilityRequestPayload(BaseModel):
    """cap.* 请求 payload（插件 -> Host 能力调用）"""
    capability: str = Field(description="能力名称，如 send.text, db.query")
    args: dict[str, Any] = Field(default_factory=dict, description="调用参数")


class CapabilityResponsePayload(BaseModel):
    """cap.* 响应 payload"""
    success: bool = Field(description="是否成功")
    result: Any = Field(default=None, description="返回值")


# ─── 健康检查 ──────────────────────────────────────────────────────

class HealthPayload(BaseModel):
    """plugin.health 响应 payload"""
    healthy: bool = Field(description="是否健康")
    loaded_plugins: list[str] = Field(default_factory=list, description="已加载的插件列表")
    uptime_ms: int = Field(default=0, description="运行时长(ms)")


# ─── 配置更新 ──────────────────────────────────────────────────────

class ConfigUpdatedPayload(BaseModel):
    """plugin.config_updated 事件 payload"""
    plugin_id: str = Field(description="插件 ID")
    config_version: str = Field(description="新配置版本")
    config_data: dict[str, Any] = Field(default_factory=dict, description="配置内容")


# ─── 关停 ──────────────────────────────────────────────────────────

class ShutdownPayload(BaseModel):
    """plugin.shutdown / plugin.prepare_shutdown payload"""
    reason: str = Field(default="normal", description="关停原因")
    drain_timeout_ms: int = Field(default=5000, description="排空超时(ms)")
